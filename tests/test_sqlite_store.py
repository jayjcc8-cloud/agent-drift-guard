from __future__ import annotations

import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from agent_drift.core import DriftEvidence, SupervisionResult
from agent_drift.protocol.decisions import (
    DecisionAction,
    DriftType,
    GuardDecision,
    Severity,
)
from agent_drift.protocol.events import AgentEvent, EventType
from agent_drift.store import RedactionPolicy, RetentionPolicy, SQLiteStore, StoreError

NOW = datetime(2026, 8, 8, tzinfo=UTC)


def event(index: int = 0, *, session_id: str = "s1") -> AgentEvent:
    return AgentEvent(
        event_type=EventType.TOOL_BEFORE,
        platform="test-agent",
        session_id=session_id,
        timestamp=NOW,
        payload={
            "tool": "shell",
            "arguments": {"command": f"echo {index}"},
            "tool_call_id": f"tool-{index}",
            "paths": [],
        },
    )


def result_for(stored: AgentEvent) -> SupervisionResult:
    evidence = DriftEvidence(
        detector="test",
        drift_type=DriftType.LOOP,
        severity=Severity.HIGH,
        score=0.9,
        event_id=stored.event_id,
        summary="Repeated failure.",
    )
    decision = GuardDecision(
        source_event_id=stored.event_id,
        action=DecisionAction.BLOCK,
        fallback_action=DecisionAction.WARN,
        severity=Severity.HIGH,
        drift_type=DriftType.LOOP,
        score=0.9,
        reason="Repeated failure.",
        evidence_ids=(evidence.evidence_id,),
    )
    return SupervisionResult(event=stored, evidence=(evidence,), decision=decision)


def test_store_initializes_private_wal_database(tmp_path: Path) -> None:
    path = tmp_path / "state" / "drift.db"
    store = SQLiteStore(path)
    assert path.exists()
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
    assert store.integrity_check() == "ok"
    assert store.stats().model_dump() == {
        "schema_version": 3,
        "sessions": 0,
        "events": 0,
        "evidence": 0,
        "decisions": 0,
        "redactions": 0,
    }
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes are not available on Windows")
def test_store_repairs_permissions_on_an_existing_database(tmp_path: Path) -> None:
    path = tmp_path / "drift.db"
    SQLiteStore(path)
    path.chmod(0o644)

    SQLiteStore(path)

    assert path.stat().st_mode & 0o777 == 0o600


def test_event_and_result_round_trip(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "drift.db")
    stored = store.prepare_event(event())
    assert stored.sequence == 0
    result = result_for(stored)
    assert store.record_result(result) == result
    assert store.get_result(stored.event_id) == result
    assert store.load_history("s1") == (stored,)
    assert store.stats().events == 1
    assert store.stats().evidence == 1
    assert store.stats().decisions == 1


def test_prepare_event_is_idempotent_and_rejects_changed_data(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "drift.db")
    original = event()
    stored = store.prepare_event(original)
    assert store.prepare_event(original) == stored
    changed = original.model_copy(update={"payload": {"tool": "different"}})
    with pytest.raises(StoreError, match="different data"):
        store.prepare_event(changed)
    changed_sequence = original.model_copy(update={"sequence": 99})
    with pytest.raises(StoreError, match="different data"):
        store.prepare_event(changed_sequence)


def test_record_result_first_writer_wins_idempotently(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "drift.db")
    stored = store.prepare_event(event())
    first = result_for(stored)
    assert store.record_result(first) == first
    competing = SupervisionResult(
        event=stored,
        evidence=(),
        decision=GuardDecision(
            source_event_id=stored.event_id,
            action=DecisionAction.ALLOW,
            reason="Competing result.",
        ),
    )
    assert store.record_result(competing) == first


def test_two_store_instances_share_monotonic_sequences(tmp_path: Path) -> None:
    path = tmp_path / "drift.db"
    stores = (SQLiteStore(path), SQLiteStore(path))

    def persist(index: int) -> int:
        stored = stores[index % 2].prepare_event(event(index))
        assert stored.sequence is not None
        return stored.sequence

    with ThreadPoolExecutor(max_workers=8) as executor:
        sequences = list(executor.map(persist, range(30)))
    assert sorted(sequences) == list(range(30))
    assert [item.sequence for item in stores[0].load_history("s1", limit=30)] == list(range(30))


def test_explicit_sequence_conflict_is_reported(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "drift.db")
    store.prepare_event(event().model_copy(update={"sequence": 7}))
    with pytest.raises(StoreError, match="sequence conflict"):
        store.prepare_event(event(2).model_copy(update={"sequence": 7}))


def test_newer_schema_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "future.db"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 99")
    with pytest.raises(StoreError, match="newer than supported"):
        SQLiteStore(path)


def test_unknown_event_result_is_rejected(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "drift.db")
    unstored = event().model_copy(update={"event_id": uuid4(), "sequence": 0})
    with pytest.raises(StoreError, match="before event"):
        store.record_result(result_for(unstored))


def test_sensitive_event_data_is_redacted_before_persistence(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "drift.db")
    original = event().model_copy(
        update={
            "payload": {
                "tool": "shell",
                "arguments": {
                    "command": "curl -H 'Authorization: Bearer abcdefghijklmnop'",
                    "OPENAI_API_KEY": "sk-1234567890abcdefghijkl",
                    "password": "correct-horse-battery-staple",
                },
            },
            "extensions": {"test.authorization": "Bearer zyxwvutsrqponmlk"},
        }
    )
    stored = store.prepare_event(original)
    serialized = stored.model_dump_json()
    assert "correct-horse" not in serialized
    assert "sk-123456" not in serialized
    assert "abcdefghijklmnop" not in serialized
    assert serialized.count("[REDACTED]") >= 4
    assert "correct-horse" in original.model_dump_json()
    assert store.stats().redactions >= 4


def test_redaction_can_be_explicitly_disabled(tmp_path: Path) -> None:
    store = SQLiteStore(
        tmp_path / "drift.db",
        redaction_policy=RedactionPolicy(enabled=False),
    )
    original = event().model_copy(update={"payload": {"password": "only-for-explicit-opt-out"}})
    assert store.prepare_event(original).payload["password"] == "only-for-explicit-opt-out"
    assert store.stats().redactions == 0


def test_retention_is_dry_run_by_default_and_cascades_on_apply(tmp_path: Path) -> None:
    path = tmp_path / "drift.db"
    store = SQLiteStore(path)
    stored = [store.prepare_event(event(index)) for index in range(3)]
    store.record_result(result_for(stored[0]))
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE events SET stored_at_epoch = ? WHERE event_id = ?",
            (int((NOW - timedelta(days=60)).timestamp()), str(stored[0].event_id)),
        )
    policy = RetentionPolicy(max_age=timedelta(days=30), max_events_per_session=1)
    preview = store.prune(policy, now=NOW)
    assert preview.matched_events == 2
    assert preview.deleted_events == 0
    assert store.stats().events == 3

    applied = store.prune(policy, now=NOW, dry_run=False)
    assert applied.matched_events == 2
    assert applied.deleted_events == 2
    assert applied.deleted_sessions == 0
    assert store.load_history("s1") == (stored[2],)
    assert store.stats().evidence == 0
    assert store.stats().decisions == 0


def test_automatic_retention_runs_once_when_store_opens(tmp_path: Path) -> None:
    path = tmp_path / "automatic.db"
    seed = SQLiteStore(path, retention_policy=None)
    stored = [seed.prepare_event(event(index)) for index in range(3)]
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE events SET stored_at_epoch = ? WHERE event_id = ?",
            (int((datetime.now(UTC) - timedelta(days=60)).timestamp()), str(stored[0].event_id)),
        )
    maintained = SQLiteStore(
        path,
        retention_policy=RetentionPolicy(
            max_age=timedelta(days=30),
            max_events_per_session=2,
        ),
    )
    assert maintained.last_automatic_prune is not None
    assert maintained.last_automatic_prune.deleted_events == 1
    assert maintained.stats().events == 2
    reopened = SQLiteStore(path)
    assert reopened.last_automatic_prune is None


def test_automatic_retention_failure_rolls_back_maintenance_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "automatic-failure.db"
    seed = SQLiteStore(path, retention_policy=None)
    seed.prepare_event(event())

    def fail_prune(
        connection: sqlite3.Connection,
        policy: RetentionPolicy,
        *,
        current: datetime,
        dry_run: bool,
    ) -> None:
        del connection, policy, current, dry_run
        raise sqlite3.OperationalError("simulated retention failure")

    monkeypatch.setattr(SQLiteStore, "_prune_connection", staticmethod(fail_prune))
    with pytest.raises(StoreError, match="failed to initialize SQLite store"):
        SQLiteStore(path)

    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT value FROM maintenance WHERE key = 'retention.last_run_epoch'"
            ).fetchone()
            is None
        )


def test_schema_one_database_migrates_transactionally(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    legacy = event().model_copy(update={"sequence": 0})
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE sessions (
                session_id TEXT PRIMARY KEY,
                next_sequence INTEGER NOT NULL
            );
            CREATE TABLE events (
                event_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions(session_id),
                sequence INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                platform TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                event_json TEXT NOT NULL,
                UNIQUE(session_id, sequence)
            );
            CREATE TABLE evidence (
                evidence_id TEXT PRIMARY KEY,
                event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
                ordinal INTEGER NOT NULL,
                evidence_json TEXT NOT NULL,
                UNIQUE(event_id, ordinal)
            );
            CREATE TABLE decisions (
                decision_id TEXT PRIMARY KEY,
                event_id TEXT NOT NULL UNIQUE REFERENCES events(event_id) ON DELETE CASCADE,
                action TEXT NOT NULL,
                decision_json TEXT NOT NULL
            );
            PRAGMA user_version = 1;
            """
        )
        connection.execute("INSERT INTO sessions VALUES (?, ?)", (legacy.session_id, 1))
        connection.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                str(legacy.event_id),
                legacy.session_id,
                legacy.sequence,
                legacy.event_type.value,
                legacy.platform,
                legacy.timestamp.isoformat(),
                legacy.model_dump_json(),
            ),
        )

    migrated = SQLiteStore(path)
    assert migrated.stats().schema_version == 3
    assert migrated.load_history("s1") == (legacy,)
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(events)")}
    assert {"stored_at_epoch", "redaction_count"} <= columns
    with sqlite3.connect(path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert "maintenance" in tables
