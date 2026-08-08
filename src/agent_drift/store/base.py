"""Persistence boundary for cross-process supervision state."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from agent_drift.core.evidence import SupervisionResult
from agent_drift.protocol.base import WireModel
from agent_drift.protocol.events import AgentEvent
from agent_drift.store.privacy import PruneResult, RetentionPolicy


class StoreError(RuntimeError):
    """Base error for durable store failures."""


class StoreConflictError(StoreError):
    """Raised when an identifier or sequence points at different immutable data."""


class StoreStats(WireModel):
    schema_version: int
    sessions: int
    events: int
    evidence: int
    decisions: int
    redactions: int


class EventStore(Protocol):
    def prepare_event(self, event: AgentEvent) -> AgentEvent:
        """Atomically assign sequence and persist an event, or return its prior copy."""

    def load_history(
        self,
        session_id: str,
        *,
        before_sequence: int | None = None,
        limit: int = 500,
    ) -> tuple[AgentEvent, ...]:
        """Load an ascending, bounded session history."""

    def get_result(self, event_id: UUID) -> SupervisionResult | None:
        """Load a completed supervision result if one exists."""

    def record_result(self, result: SupervisionResult) -> SupervisionResult:
        """Atomically persist evidence and decision, returning the winning result."""

    def stats(self) -> StoreStats:
        """Return inexpensive row counts and the schema version."""

    def integrity_check(self) -> str:
        """Run SQLite-style integrity verification and return its status."""

    def prune(
        self,
        policy: RetentionPolicy,
        *,
        now: datetime | None = None,
        dry_run: bool = True,
    ) -> PruneResult:
        """Preview or apply bounded retention cleanup."""
