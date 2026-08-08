"""Composition layer joining one native adapter to one Supervisor instance."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from agent_drift.adapters import HookResponse, PlatformAdapter
from agent_drift.core import SupervisionResult, Supervisor
from agent_drift.protocol.base import WireModel


class RuntimeOutcome(WireModel):
    supervision: SupervisionResult
    response: HookResponse


class AgentDriftRuntime:
    """Handle native hooks end to end inside a long-lived process."""

    def __init__(self, adapter: PlatformAdapter, supervisor: Supervisor) -> None:
        self._adapter = adapter
        self._supervisor = supervisor

    def handle(
        self,
        raw: dict[str, Any],
        *,
        timestamp: datetime | None = None,
        repo_root: str | None = None,
        sequence: int | None = None,
    ) -> RuntimeOutcome:
        event = self._adapter.adapt_event(
            raw,
            timestamp=timestamp,
            repo_root=repo_root,
            sequence=sequence,
        )
        supervision = self._supervisor.process(event)
        response = self._adapter.render_decision(supervision.event, supervision.decision)
        return RuntimeOutcome(supervision=supervision, response=response)
