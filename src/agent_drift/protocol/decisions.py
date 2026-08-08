"""Platform-neutral Guard Decision contract v0.1."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field, JsonValue, model_validator

from agent_drift.protocol.base import WireModel
from agent_drift.protocol.versioning import PROTOCOL_VERSION, ProtocolVersion


class DecisionAction(StrEnum):
    ALLOW = "allow"
    WARN = "warn"
    BLOCK = "block"
    REANCHOR = "reanchor"
    RETRY = "retry"
    CONTINUE = "continue"
    ABORT = "abort"


class Severity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class DriftType(StrEnum):
    GOAL = "goal_drift"
    CONSTRAINT = "constraint_drift"
    SCOPE = "scope_drift"
    PLAN = "plan_drift"
    STATE = "state_drift"
    DECISION = "decision_drift"
    VALIDATION = "validation_drift"
    LOOP = "loop_drift"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class GuardDecision(WireModel):
    """A core decision that adapters translate into platform-specific behavior."""

    protocol_version: ProtocolVersion = Field(
        default_factory=lambda: ProtocolVersion(PROTOCOL_VERSION)
    )
    decision_id: UUID = Field(default_factory=uuid4)
    source_event_id: UUID | None = None
    action: DecisionAction
    fallback_action: DecisionAction | None = None
    severity: Severity = Severity.INFO
    drift_type: DriftType | None = None
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    reason: str = Field(min_length=1, max_length=8192)
    context: str | None = Field(default=None, max_length=32768)
    evidence_ids: tuple[UUID, ...] = ()
    retry_after_seconds: float | None = Field(default=None, ge=0.0)
    max_retries: int | None = Field(default=None, ge=1)
    created_at: datetime = Field(default_factory=_utc_now)
    extensions: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_action_contract(self) -> GuardDecision:
        if self.fallback_action == self.action:
            raise ValueError("fallback_action must differ from action")
        if self.action == DecisionAction.RETRY and self.max_retries is None:
            raise ValueError("retry decisions require max_retries")
        if self.action != DecisionAction.RETRY and (
            self.retry_after_seconds is not None or self.max_retries is not None
        ):
            raise ValueError("retry controls are only valid for retry decisions")
        if self.action == DecisionAction.ALLOW and self.drift_type:
            raise ValueError("allow decisions cannot declare a drift_type")
        invalid = sorted(key for key in self.extensions if "." not in key)
        if invalid:
            raise ValueError("extension keys must be namespaced: " + ", ".join(invalid))
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must include a timezone offset")
        return self

    def assert_supported(self, supported: str | ProtocolVersion = PROTOCOL_VERSION) -> None:
        if not self.protocol_version.is_compatible_with(supported):
            raise ValueError(
                f"decision protocol {self.protocol_version} is not compatible with {supported}"
            )
