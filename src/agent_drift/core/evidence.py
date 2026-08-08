"""Auditable evidence and supervision results."""

from __future__ import annotations

from uuid import UUID, uuid4

from pydantic import Field, JsonValue

from agent_drift.protocol.base import WireModel
from agent_drift.protocol.decisions import DriftType, GuardDecision, Severity
from agent_drift.protocol.events import AgentEvent


class DriftEvidence(WireModel):
    evidence_id: UUID = Field(default_factory=uuid4)
    detector: str = Field(min_length=1, max_length=512)
    drift_type: DriftType
    severity: Severity
    score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    summary: str = Field(min_length=1, max_length=8192)
    event_id: UUID
    facts: dict[str, JsonValue] = Field(default_factory=dict)


class SupervisionResult(WireModel):
    event: AgentEvent
    evidence: tuple[DriftEvidence, ...]
    decision: GuardDecision
