"""Unified Agent Event Protocol v0.1."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import Field, JsonValue, field_validator

from agent_drift.protocol.base import WireModel
from agent_drift.protocol.versioning import PROTOCOL_VERSION, ProtocolVersion


class EventType(StrEnum):
    SESSION_START = "session.start"
    PROMPT_SUBMIT = "prompt.submit"
    TOOL_BEFORE = "tool.before"
    TOOL_AFTER = "tool.after"
    TOOL_ERROR = "tool.error"
    COMPACTION_BEFORE = "compaction.before"
    COMPACTION_AFTER = "compaction.after"
    SUBAGENT_START = "subagent.start"
    SUBAGENT_STOP = "subagent.stop"
    AGENT_STOP = "agent.stop"
    SESSION_END = "session.end"


NonEmptyStr = Annotated[str, Field(min_length=1, max_length=512)]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class AgentEvent(WireModel):
    """Canonical event envelope emitted by every platform adapter.

    `payload` contains normalized event data. Adapter-specific data belongs in
    namespaced `extensions`, keeping the core protocol portable.
    """

    protocol_version: ProtocolVersion = Field(
        default_factory=lambda: ProtocolVersion(PROTOCOL_VERSION)
    )
    event_id: UUID = Field(default_factory=uuid4)
    event_type: EventType
    platform: NonEmptyStr
    platform_version: str | None = Field(default=None, max_length=128)
    session_id: NonEmptyStr
    turn_id: str | None = Field(default=None, max_length=512)
    agent_id: str = Field(default="main", min_length=1, max_length=512)
    repo_root: str | None = Field(default=None, max_length=4096)
    cwd: str | None = Field(default=None, max_length=4096)
    timestamp: datetime = Field(default_factory=_utc_now)
    sequence: int | None = Field(default=None, ge=0)
    parent_event_id: UUID | None = None
    trace_id: str | None = Field(default=None, min_length=1, max_length=256)
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    extensions: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_have_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone offset")
        return value

    @field_validator("extensions")
    @classmethod
    def extensions_must_be_namespaced(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        invalid = sorted(key for key in value if "." not in key)
        if invalid:
            raise ValueError(
                "extension keys must be namespaced (for example 'codex.raw_event'): "
                + ", ".join(invalid)
            )
        return value

    def assert_supported(self, supported: str | ProtocolVersion = PROTOCOL_VERSION) -> None:
        if not self.protocol_version.is_compatible_with(supported):
            raise ValueError(
                f"event protocol {self.protocol_version} is not compatible with {supported}"
            )
