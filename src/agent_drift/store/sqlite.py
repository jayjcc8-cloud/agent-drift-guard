"""SQLite WAL store for events, evidence, and decisions."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from agent_drift.core.evidence import DriftEvidence, SupervisionResult
from agent_drift.protocol.decisions import GuardDecision
from agent_drift.protocol.events import AgentEvent
from agent_drift.store.base import StoreConflictError, StoreError, StoreStats
from agent_drift.store.privacy import (
    EventRedactor,
    PruneResult,
    RedactionPolicy,
    RetentionPolicy,
)

SCHEMA_VERSION = 3
_DEFAULT_RETENTION_POLICY = RetentionPolicy()

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        next_sequence INTEGER NOT NULL CHECK (next_sequence >= 0)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        event_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES sessions(session_id),
        sequence INTEGER NOT NULL CHECK (sequence >= 0),
        event_type TEXT NOT NULL,
        platform TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        stored_at_epoch INTEGER NOT NULL CHECK (stored_at_epoch >= 0),
        redaction_count INTEGER NOT NULL DEFAULT 0 CHECK (redaction_count >= 0),
        event_json TEXT NOT NULL,
        UNIQUE(session_id, sequence)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS events_session_sequence
    ON events(session_id, sequence)
    """,
    """
    CREATE TABLE IF NOT EXISTS evidence (
        evidence_id TEXT PRIMARY KEY,
        event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
        ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
        evidence_json TEXT NOT NULL,
        UNIQUE(event_id, ordinal)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS evidence_event
    ON evidence(event_id, ordinal)
    """,
    """
    CREATE TABLE IF NOT EXISTS decisions (
        decision_id TEXT PRIMARY KEY,
        event_id TEXT NOT NULL UNIQUE REFERENCES events(event_id) ON DELETE CASCADE,
        action TEXT NOT NULL,
        decision_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS maintenance (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
)


def _migrate_1_to_2(connection: sqlite3.Connection) -> None:
    connection.execute("ALTER TABLE events ADD COLUMN stored_at_epoch INTEGER NOT NULL DEFAULT 0")
    connection.execute("ALTER TABLE events ADD COLUMN redaction_count INTEGER NOT NULL DEFAULT 0")
    connection.execute(
        """
        UPDATE events
        SET stored_at_epoch = COALESCE(CAST(strftime('%s', timestamp) AS INTEGER), unixepoch())
        WHERE stored_at_epoch = 0
        """
    )


def _migrate_2_to_3(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS maintenance (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )


_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    1: _migrate_1_to_2,
    2: _migrate_2_to_3,
}


class SQLiteStore:
    """Connection-per-operation SQLite store safe for independent Hook processes."""

    def __init__(
        self,
        path: str | Path,
        *,
        timeout_seconds: float = 10.0,
        redaction_policy: RedactionPolicy | None = None,
        retention_policy: RetentionPolicy | None = _DEFAULT_RETENTION_POLICY,
        retention_interval: timedelta = timedelta(days=1),
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if retention_interval <= timedelta(0):
            raise ValueError("retention_interval must be positive")
        self.path = Path(path).expanduser().resolve()
        self._timeout_seconds = timeout_seconds
        self._redactor = EventRedactor(redaction_policy)
        self._retention_policy = retention_policy
        self._retention_interval = retention_interval
        self.last_automatic_prune: PruneResult | None = None
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=self._timeout_seconds,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {int(self._timeout_seconds * 1000)}")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        with self._connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def _initialize(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            with self._transaction(immediate=True) as connection:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if version > SCHEMA_VERSION:
                    raise StoreError(
                        f"database schema {version} is newer than supported {SCHEMA_VERSION}"
                    )
                if version == 0:
                    for statement in _SCHEMA:
                        connection.execute(statement)
                    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                else:
                    while version < SCHEMA_VERSION:
                        migration = _MIGRATIONS.get(version)
                        if migration is None:
                            raise StoreError(
                                f"no migration from database schema {version} to {version + 1}"
                            )
                        migration(connection)
                        version += 1
                        connection.execute(f"PRAGMA user_version = {version}")
                if self._retention_policy is not None:
                    now_epoch = int(datetime.now(UTC).timestamp())
                    row = connection.execute(
                        "SELECT value FROM maintenance WHERE key = 'retention.last_run_epoch'"
                    ).fetchone()
                    last_run = int(row["value"]) if row is not None else None
                    interval_seconds = int(self._retention_interval.total_seconds())
                    if last_run is None or now_epoch - last_run >= interval_seconds:
                        self.last_automatic_prune = self._prune_connection(
                            connection,
                            self._retention_policy,
                            current=datetime.fromtimestamp(now_epoch, UTC),
                            dry_run=False,
                        )
                        connection.execute(
                            """
                            INSERT INTO maintenance(key, value)
                            VALUES ('retention.last_run_epoch', ?)
                            ON CONFLICT(key) DO UPDATE SET value = excluded.value
                            """,
                            (str(now_epoch),),
                        )
        except sqlite3.Error as exc:
            raise StoreError(f"failed to initialize SQLite store {self.path}: {exc}") from exc
        if os.name != "nt":
            with suppress(OSError):
                os.chmod(self.path, 0o600)

    @staticmethod
    def _same_event(existing: AgentEvent, candidate: AgentEvent) -> bool:
        if candidate.sequence is not None and candidate.sequence != existing.sequence:
            return False
        normalized = candidate.model_copy(update={"sequence": existing.sequence})
        return normalized == existing

    def prepare_event(self, event: AgentEvent) -> AgentEvent:
        """Reserve a per-session sequence under `BEGIN IMMEDIATE` and insert once."""

        candidate, redaction_count = self._redactor.redact_event(event)
        try:
            with self._transaction(immediate=True) as connection:
                row = connection.execute(
                    "SELECT event_json FROM events WHERE event_id = ?",
                    (str(candidate.event_id),),
                ).fetchone()
                if row is not None:
                    existing = AgentEvent.model_validate_json(row["event_json"])
                    if not self._same_event(existing, candidate):
                        raise StoreConflictError(
                            f"event id {candidate.event_id} already stores different data"
                        )
                    return existing

                connection.execute(
                    "INSERT OR IGNORE INTO sessions(session_id, next_sequence) VALUES (?, 0)",
                    (candidate.session_id,),
                )
                row = connection.execute(
                    "SELECT next_sequence FROM sessions WHERE session_id = ?",
                    (candidate.session_id,),
                ).fetchone()
                if row is None:
                    raise StoreError(f"failed to create session {candidate.session_id!r}")
                next_sequence = int(row["next_sequence"])
                sequence = next_sequence if candidate.sequence is None else candidate.sequence
                stored = candidate.model_copy(update={"sequence": sequence})
                connection.execute(
                    """
                    UPDATE sessions
                    SET next_sequence = MAX(next_sequence, ?)
                    WHERE session_id = ?
                    """,
                    (sequence + 1, candidate.session_id),
                )
                connection.execute(
                    """
                    INSERT INTO events(
                        event_id, session_id, sequence, event_type, platform, timestamp,
                        stored_at_epoch, redaction_count, event_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(stored.event_id),
                        stored.session_id,
                        sequence,
                        stored.event_type.value,
                        stored.platform,
                        stored.timestamp.isoformat(),
                        int(datetime.now(UTC).timestamp()),
                        redaction_count,
                        stored.model_dump_json(),
                    ),
                )
                return stored
        except StoreError:
            raise
        except sqlite3.IntegrityError as exc:
            raise StoreConflictError(
                f"event sequence conflict for session {candidate.session_id!r}: {exc}"
            ) from exc
        except sqlite3.Error as exc:
            raise StoreError(f"failed to persist event {candidate.event_id}: {exc}") from exc

    def load_history(
        self,
        session_id: str,
        *,
        before_sequence: int | None = None,
        limit: int = 500,
    ) -> tuple[AgentEvent, ...]:
        if limit < 1:
            raise ValueError("history limit must be positive")
        query = "SELECT event_json FROM events WHERE session_id = ?"
        parameters: list[object] = [session_id]
        if before_sequence is not None:
            query += " AND sequence < ?"
            parameters.append(before_sequence)
        query += " ORDER BY sequence DESC LIMIT ?"
        parameters.append(limit)
        try:
            with self._connection() as connection:
                rows = connection.execute(query, parameters).fetchall()
        except sqlite3.Error as exc:
            raise StoreError(f"failed to load session history: {exc}") from exc
        events = [AgentEvent.model_validate_json(row["event_json"]) for row in rows]
        events.reverse()
        return tuple(events)

    def count_session_events(self, session_id: str) -> int:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT COUNT(*) FROM events WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise StoreError(f"failed to count session events: {exc}") from exc
        return int(row[0]) if row is not None else 0

    @staticmethod
    def _load_result(connection: sqlite3.Connection, event_id: UUID) -> SupervisionResult | None:
        event_row = connection.execute(
            "SELECT event_json FROM events WHERE event_id = ?",
            (str(event_id),),
        ).fetchone()
        decision_row = connection.execute(
            "SELECT decision_json FROM decisions WHERE event_id = ?",
            (str(event_id),),
        ).fetchone()
        if event_row is None or decision_row is None:
            return None
        evidence_rows = connection.execute(
            "SELECT evidence_json FROM evidence WHERE event_id = ? ORDER BY ordinal",
            (str(event_id),),
        ).fetchall()
        return SupervisionResult(
            event=AgentEvent.model_validate_json(event_row["event_json"]),
            evidence=tuple(
                DriftEvidence.model_validate_json(row["evidence_json"]) for row in evidence_rows
            ),
            decision=GuardDecision.model_validate_json(decision_row["decision_json"]),
        )

    def get_result(self, event_id: UUID) -> SupervisionResult | None:
        try:
            with self._connection() as connection:
                return self._load_result(connection, event_id)
        except sqlite3.Error as exc:
            raise StoreError(f"failed to load supervision result: {exc}") from exc

    def record_result(self, result: SupervisionResult) -> SupervisionResult:
        event_id = str(result.event.event_id)
        try:
            with self._transaction(immediate=True) as connection:
                event_row = connection.execute(
                    "SELECT event_json FROM events WHERE event_id = ?", (event_id,)
                ).fetchone()
                if event_row is None:
                    raise StoreConflictError(
                        f"cannot record result before event {result.event.event_id}"
                    )
                existing_event = AgentEvent.model_validate_json(event_row["event_json"])
                if existing_event != result.event:
                    raise StoreConflictError(
                        f"result event {result.event.event_id} differs from stored event"
                    )
                decision_row = connection.execute(
                    "SELECT decision_json FROM decisions WHERE event_id = ?", (event_id,)
                ).fetchone()
                if decision_row is not None:
                    existing = self._load_result(connection, result.event.event_id)
                    if existing is None:
                        raise StoreError("decision exists without a readable supervision result")
                    return existing
                for ordinal, evidence in enumerate(result.evidence):
                    connection.execute(
                        """
                        INSERT INTO evidence(evidence_id, event_id, ordinal, evidence_json)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            str(evidence.evidence_id),
                            event_id,
                            ordinal,
                            evidence.model_dump_json(),
                        ),
                    )
                connection.execute(
                    """
                    INSERT INTO decisions(decision_id, event_id, action, decision_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        str(result.decision.decision_id),
                        event_id,
                        result.decision.action.value,
                        result.decision.model_dump_json(),
                    ),
                )
                return result
        except StoreError:
            raise
        except sqlite3.Error as exc:
            raise StoreError(f"failed to record supervision result: {exc}") from exc

    def stats(self) -> StoreStats:
        try:
            with self._connection() as connection:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                counts = {
                    table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                    for table in ("sessions", "events", "evidence", "decisions")
                }
                redactions = int(
                    connection.execute(
                        "SELECT COALESCE(SUM(redaction_count), 0) FROM events"
                    ).fetchone()[0]
                )
        except sqlite3.Error as exc:
            raise StoreError(f"failed to read store statistics: {exc}") from exc
        return StoreStats(schema_version=version, redactions=redactions, **counts)

    def integrity_check(self) -> str:
        try:
            with self._connection() as connection:
                row = connection.execute("PRAGMA integrity_check").fetchone()
        except sqlite3.Error as exc:
            raise StoreError(f"failed to check store integrity: {exc}") from exc
        return str(row[0]) if row is not None else "unknown"

    def prune(
        self,
        policy: RetentionPolicy,
        *,
        now: datetime | None = None,
        dry_run: bool = True,
    ) -> PruneResult:
        current = now or datetime.now(UTC)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("retention time must include a timezone offset")
        try:
            with self._transaction(immediate=True) as connection:
                return self._prune_connection(connection, policy, current=current, dry_run=dry_run)
        except sqlite3.Error as exc:
            raise StoreError(f"failed to apply retention policy: {exc}") from exc

    @staticmethod
    def _prune_connection(
        connection: sqlite3.Connection,
        policy: RetentionPolicy,
        *,
        current: datetime,
        dry_run: bool,
    ) -> PruneResult:
        event_ids: set[str] = set()
        if policy.max_age is not None:
            cutoff = int((current - policy.max_age).timestamp())
            event_ids.update(
                str(row["event_id"])
                for row in connection.execute(
                    "SELECT event_id FROM events WHERE stored_at_epoch < ?",
                    (cutoff,),
                )
            )
        if policy.max_events_per_session is not None:
            event_ids.update(
                str(row["event_id"])
                for row in connection.execute(
                    """
                    SELECT event_id
                    FROM (
                        SELECT event_id,
                               ROW_NUMBER() OVER (
                                   PARTITION BY session_id ORDER BY sequence DESC
                               ) AS session_rank
                        FROM events
                    )
                    WHERE session_rank > ?
                    """,
                    (policy.max_events_per_session,),
                )
            )
        if dry_run:
            return PruneResult(
                matched_events=len(event_ids),
                deleted_events=0,
                deleted_sessions=0,
                dry_run=True,
            )
        connection.executemany(
            "DELETE FROM events WHERE event_id = ?",
            ((event_id,) for event_id in event_ids),
        )
        empty_sessions = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM sessions
                WHERE NOT EXISTS (
                    SELECT 1 FROM events WHERE events.session_id = sessions.session_id
                )
                """
            ).fetchone()[0]
        )
        connection.execute(
            """
            DELETE FROM sessions
            WHERE NOT EXISTS (
                SELECT 1 FROM events WHERE events.session_id = sessions.session_id
            )
            """
        )
        return PruneResult(
            matched_events=len(event_ids),
            deleted_events=len(event_ids),
            deleted_sessions=empty_sessions,
            dry_run=False,
        )
