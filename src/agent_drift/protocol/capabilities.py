"""Platform capability negotiation and protection-level assessment."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, computed_field, field_serializer

from agent_drift.protocol.base import WireModel
from agent_drift.protocol.versioning import PROTOCOL_VERSION, ProtocolVersion


class Capability(StrEnum):
    OBSERVE_SESSION = "observe.session"
    OBSERVE_PROMPT = "observe.prompt"
    OBSERVE_TOOL = "observe.tool"
    OBSERVE_TOOL_RESULT = "observe.tool_result"
    OBSERVE_COMPACTION = "observe.compaction"
    OBSERVE_SUBAGENT = "observe.subagent"
    OBSERVE_STOP = "observe.stop"
    BLOCK_TOOL = "control.block_tool"
    MODIFY_TOOL = "control.modify_tool"
    INJECT_CONTEXT = "control.inject_context"
    BLOCK_STOP = "control.block_stop"
    RETRY = "control.retry"
    ABORT = "control.abort"


class ProtectionLevel(StrEnum):
    NONE = "none"
    AUDIT = "audit"
    PARTIAL = "partial"
    FULL = "full"


FULL_GUARD_REQUIREMENTS = frozenset(
    {
        Capability.OBSERVE_PROMPT,
        Capability.OBSERVE_TOOL,
        Capability.OBSERVE_TOOL_RESULT,
        Capability.OBSERVE_COMPACTION,
        Capability.OBSERVE_SUBAGENT,
        Capability.OBSERVE_STOP,
        Capability.BLOCK_TOOL,
        Capability.INJECT_CONTEXT,
        Capability.BLOCK_STOP,
    }
)


class CapabilityAssessment(WireModel):
    required: frozenset[Capability]
    supported: frozenset[Capability]
    missing: frozenset[Capability]

    @field_serializer("required", "supported", "missing", when_used="json")
    def serialize_capability_sets(self, value: frozenset[Capability]) -> list[str]:
        return sorted(item.value for item in value)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def coverage(self) -> float:
        if not self.required:
            return 1.0
        return len(self.supported) / len(self.required)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def satisfied(self) -> bool:
        return not self.missing


class PlatformCapabilities(WireModel):
    """Capabilities advertised by one adapter/platform pair."""

    protocol_version: ProtocolVersion = Field(
        default_factory=lambda: ProtocolVersion(PROTOCOL_VERSION)
    )
    platform: str = Field(min_length=1, max_length=512)
    platform_version: str | None = Field(default=None, max_length=128)
    adapter_version: str = Field(min_length=1, max_length=128)
    capabilities: frozenset[Capability] = frozenset()
    notes: tuple[str, ...] = ()

    @field_serializer("capabilities", when_used="json")
    def serialize_capabilities(self, value: frozenset[Capability]) -> list[str]:
        return sorted(item.value for item in value)

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities

    def assess(
        self, required: frozenset[Capability] = FULL_GUARD_REQUIREMENTS
    ) -> CapabilityAssessment:
        supported = required & self.capabilities
        return CapabilityAssessment(
            required=required,
            supported=supported,
            missing=required - supported,
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def protection_level(self) -> ProtectionLevel:
        if not self.capabilities:
            return ProtectionLevel.NONE
        assessment = self.assess()
        if assessment.satisfied:
            return ProtectionLevel.FULL
        has_control = any(item.value.startswith("control.") for item in self.capabilities)
        return ProtectionLevel.PARTIAL if has_control else ProtectionLevel.AUDIT

    def assert_supported(self, supported: str | ProtocolVersion = PROTOCOL_VERSION) -> None:
        if not self.protocol_version.is_compatible_with(supported):
            raise ValueError(
                f"capabilities protocol {self.protocol_version} is not compatible with {supported}"
            )
