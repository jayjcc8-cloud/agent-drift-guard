"""Process-safe local observability envelopes and exporters."""

from __future__ import annotations

import os
from collections.abc import Iterable
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


class JsonlExporter:
    """Append one observation per write for independent command Hook processes."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    def export(self, observation: ObservationEnvelope) -> None:
        payload = observation.model_dump_json(exclude_none=True).encode("utf-8") + b"\n"
        descriptor = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            if os.name != "nt":
                os.fchmod(descriptor, 0o600)
            written = os.write(descriptor, payload)
            if written != len(payload):
                raise OSError(f"short telemetry write: {written} of {len(payload)} bytes")
        finally:
            os.close(descriptor)


class CompositeExporter:
    def __init__(self, exporters: Iterable[ObservationExporter]) -> None:
        self._exporters = tuple(exporters)

    def export(self, observation: ObservationEnvelope) -> None:
        for exporter in self._exporters:
            exporter.export(observation)
