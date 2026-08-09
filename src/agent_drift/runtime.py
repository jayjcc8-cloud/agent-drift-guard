"""Composition layer joining one native adapter to one Supervisor instance."""

from __future__ import annotations

from datetime import datetime
from time import perf_counter_ns
from typing import Any

from agent_drift.adapters import HookResponse, PlatformAdapter
from agent_drift.core import SupervisionResult, Supervisor
from agent_drift.observability import ObservationEnvelope, ObservationExporter
from agent_drift.protocol.base import WireModel


class RuntimeOutcome(WireModel):
    supervision: SupervisionResult
    response: HookResponse
    processing_duration_ms: float = 0.0
    export_error: str | None = None


class AgentDriftRuntime:
    """Handle native hooks end to end inside a long-lived process."""

    def __init__(
        self,
        adapter: PlatformAdapter,
        supervisor: Supervisor,
        *,
        exporter: ObservationExporter | None = None,
    ) -> None:
        self._adapter = adapter
        self._supervisor = supervisor
        self._exporter = exporter

    def handle(
        self,
        raw: dict[str, Any],
        *,
        timestamp: datetime | None = None,
        repo_root: str | None = None,
        sequence: int | None = None,
    ) -> RuntimeOutcome:
        started = perf_counter_ns()
        event = self._adapter.adapt_event(
            raw,
            timestamp=timestamp,
            repo_root=repo_root,
            sequence=sequence,
        )
        supervision = self._supervisor.process(event)
        response = self._adapter.render_decision(supervision.event, supervision.decision)
        duration_ms = (perf_counter_ns() - started) / 1_000_000
        export_error: str | None = None
        if self._exporter is not None:
            try:
                self._exporter.export(
                    ObservationEnvelope(
                        processing_duration_ms=duration_ms,
                        supervision=supervision,
                        response=response,
                    )
                )
            except Exception as exc:  # Export must never change the guard decision.
                export_error = str(exc)
        return RuntimeOutcome(
            supervision=supervision,
            response=response,
            processing_duration_ms=duration_ms,
            export_error=export_error,
        )
