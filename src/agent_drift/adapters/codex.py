"""Codex native lifecycle Hook adapter."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from agent_drift.adapters.base import HookResponse, PlatformAdapter
from agent_drift.adapters.decision_mapping import common_hook_response, render_with_fallback
from agent_drift.adapters.normalization import build_event
from agent_drift.protocol.capabilities import Capability, PlatformCapabilities
from agent_drift.protocol.decisions import GuardDecision
from agent_drift.protocol.events import AgentEvent

_CODEX_HOOKS = frozenset(
    {
        "SessionStart",
        "UserPromptSubmit",
        "PermissionRequest",
        "PreToolUse",
        "PostToolUse",
        "PreCompact",
        "PostCompact",
        "SubagentStart",
        "SubagentStop",
        "Stop",
        "SessionEnd",
    }
)


class CodexAdapter(PlatformAdapter):
    """Adapter for the current Codex command-hook JSON contract."""

    def __init__(self, platform_version: str | None = None) -> None:
        self._platform_version = platform_version

    @property
    def capabilities(self) -> PlatformCapabilities:
        return PlatformCapabilities(
            platform="codex",
            platform_version=self._platform_version,
            adapter_version="0.8.0",
            capabilities=frozenset(
                {
                    Capability.OBSERVE_SESSION,
                    Capability.OBSERVE_PROMPT,
                    Capability.OBSERVE_TOOL,
                    Capability.OBSERVE_TOOL_RESULT,
                    Capability.OBSERVE_COMPACTION,
                    Capability.OBSERVE_SUBAGENT,
                    Capability.OBSERVE_STOP,
                    Capability.BLOCK_TOOL,
                    Capability.MODIFY_TOOL,
                    Capability.INJECT_CONTEXT,
                    Capability.BLOCK_STOP,
                }
            ),
            notes=(
                "Hosted and specialized tool paths may bypass the default local tool hook path.",
                "SessionEnd is advisory and cannot keep a thread open.",
            ),
        )

    def adapt_event(
        self,
        raw: Mapping[str, Any],
        *,
        timestamp: datetime | None = None,
        repo_root: str | None = None,
        sequence: int | None = None,
    ) -> AgentEvent:
        return build_event(
            platform="codex",
            platform_version=self._platform_version,
            extension_namespace="codex",
            allowed_hooks=_CODEX_HOOKS,
            raw_input=raw,
            timestamp=timestamp,
            repo_root=repo_root,
            sequence=sequence,
        )

    def render_decision(self, event: AgentEvent, decision: GuardDecision) -> HookResponse:
        return render_with_fallback(
            event,
            decision,
            lambda current, guard_decision, action: common_hook_response(
                current,
                guard_decision,
                action,
                precompact_uses_continue=True,
            ),
        )
