"""Process-safe local observability envelopes and exporters."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from pydantic import Field

from agent_drift.adapters import HookResponse
from agent_drift.core import SupervisionResult
from agent_drift.protocol.base import WireModel


class ObservationEnvelope(WireModel):
    schema_version: str = "0.1"
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    processing_duration_ms: float = Field(ge=0)
    supervision: SupervisionResult
    response: HookResponse


class ObservationExporter(Protocol):
    def export(self, observation: ObservationEnvelope) -> None:
        """Export one completed runtime observation."""


class JsonlExporterHealth(WireModel):
    path: str
    current_bytes: int
    rotated_files: int
    failure_count: int
    last_failure_at: datetime | None = None
    last_error: str | None = None


class JsonlExporter:
    """Append bounded observations with process-safe rotation and persistent failures."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_bytes: int = 32 * 1024 * 1024,
        backup_count: int = 3,
        max_record_bytes: int = 1024 * 1024,
    ) -> None:
        if max_bytes < 1:
            raise ValueError("telemetry max_bytes must be positive")
        if backup_count < 0:
            raise ValueError("telemetry backup_count cannot be negative")
        if max_record_bytes < 1 or max_record_bytes > max_bytes:
            raise ValueError(
                "telemetry max_record_bytes must be positive and no larger than max_bytes"
            )
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.max_record_bytes = max_record_bytes
        self._lock_path = self.path.with_name(f".{self.path.name}.lock")
        self._health_path = self.path.with_name(f".{self.path.name}.health.json")

    def _lock(self) -> int:
        descriptor = os.open(self._lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        if os.name != "nt":
            import fcntl

            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        return descriptor

    @staticmethod
    def _unlock(descriptor: int) -> None:
        if os.name != "nt":
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)

    def _rotate(self) -> None:
        if self.backup_count == 0:
            self.path.unlink(missing_ok=True)
            return
        oldest = self.path.with_name(f"{self.path.name}.{self.backup_count}")
        oldest.unlink(missing_ok=True)
        for index in range(self.backup_count - 1, 0, -1):
            source = self.path.with_name(f"{self.path.name}.{index}")
            if source.exists():
                os.replace(source, self.path.with_name(f"{self.path.name}.{index + 1}"))
        if self.path.exists():
            os.replace(self.path, self.path.with_name(f"{self.path.name}.1"))

    def _write_payload(self, payload: bytes) -> None:
        current_size = self.path.stat().st_size if self.path.exists() else 0
        if current_size and current_size + len(payload) > self.max_bytes:
            self._rotate()
        descriptor = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            if os.name != "nt":
                os.fchmod(descriptor, 0o600)
            offset = 0
            while offset < len(payload):
                written = os.write(descriptor, payload[offset:])
                if written < 1:
                    raise OSError(f"short telemetry write after {offset} of {len(payload)} bytes")
                offset += written
        finally:
            os.close(descriptor)

    def _read_failure_health(self) -> tuple[int, datetime | None, str | None]:
        if not self._health_path.exists():
            return 0, None, None
        try:
            health = JsonlExporterHealth.model_validate_json(
                self._health_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return 0, None, None
        return health.failure_count, health.last_failure_at, health.last_error

    def _record_failure(self, error: Exception) -> None:
        count, _, _ = self._read_failure_health()
        health = JsonlExporterHealth(
            path=str(self.path),
            current_bytes=self.path.stat().st_size if self.path.exists() else 0,
            rotated_files=sum(
                self.path.with_name(f"{self.path.name}.{index}").exists()
                for index in range(1, self.backup_count + 1)
            ),
            failure_count=count + 1,
            last_failure_at=datetime.now(UTC),
            last_error=str(error)[:4096],
        )
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self._health_path.name}.", dir=self.path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(health.model_dump_json(exclude_none=True))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._health_path)
            if os.name != "nt":
                os.chmod(self._health_path, 0o600)
        except Exception:
            temporary.unlink(missing_ok=True)

    def _safe_record_failure(self, error: Exception) -> None:
        with suppress(Exception):
            self._record_failure(error)

    def status(self) -> JsonlExporterHealth:
        count, last_failure_at, last_error = self._read_failure_health()
        return JsonlExporterHealth(
            path=str(self.path),
            current_bytes=self.path.stat().st_size if self.path.exists() else 0,
            rotated_files=sum(
                self.path.with_name(f"{self.path.name}.{index}").exists()
                for index in range(1, self.backup_count + 1)
            ),
            failure_count=count,
            last_failure_at=last_failure_at,
            last_error=last_error,
        )

    def export(self, observation: ObservationEnvelope) -> None:
        payload = observation.model_dump_json(exclude_none=True).encode("utf-8") + b"\n"
        if len(payload) > self.max_record_bytes:
            error = ValueError(
                f"telemetry record is {len(payload)} bytes; limit is {self.max_record_bytes}"
            )
            lock = self._lock()
            try:
                self._safe_record_failure(error)
            finally:
                self._unlock(lock)
            raise error
        lock = self._lock()
        try:
            try:
                self._write_payload(payload)
            except Exception as exc:
                self._safe_record_failure(exc)
                raise
        finally:
            self._unlock(lock)


class CompositeExporter:
    def __init__(self, exporters: Iterable[ObservationExporter]) -> None:
        self._exporters = tuple(exporters)

    def export(self, observation: ObservationEnvelope) -> None:
        for exporter in self._exporters:
            exporter.export(observation)
