"""Shared native decision translation with explicit fallback handling."""

from __future__ import annotations

from collections.abc import Callable

from agent_drift.adapters.base import HookResponse, UnsupportedDecisionError
from agent_drift.protocol.decisions import DecisionAction, GuardDecision
from agent_drift.protocol.events import AgentEvent, EventType

Renderer = Callable[[AgentEvent, GuardDecision, DecisionAction], HookResponse | None]


def render_with_fallback(
    event: AgentEvent, decision: GuardDecision, renderer: Renderer
) -> HookResponse:
    response = renderer(event, decision, decision.action)
    if response is not None:
        return response
    if decision.fallback_action is not None:
        response = renderer(event, decision, decision.fallback_action)
        if response is not None:
            return response
    raise UnsupportedDecisionError(
        f"{event.platform} cannot apply {decision.action.value} to {event.event_type.value}"
    )


def common_hook_response(
    event: AgentEvent,
    decision: GuardDecision,
    action: DecisionAction,
    *,
    precompact_uses_continue: bool,
) -> HookResponse | None:
    hook_name_value = event.extensions.get(f"{event.platform}.hook_event_name")
    if not isinstance(hook_name_value, str):
        raise UnsupportedDecisionError("event is missing its native hook event name")
    hook_name = hook_name_value
    applied = action.value
    reason = decision.context or decision.reason

    if action == DecisionAction.ALLOW:
        return HookResponse(applied_action=applied)
    if action == DecisionAction.WARN:
        return HookResponse(stdout={"systemMessage": decision.reason}, applied_action=applied)
    if action == DecisionAction.RETRY:
        return None
    if action == DecisionAction.CONTINUE:
        if event.event_type not in {EventType.AGENT_STOP, EventType.SUBAGENT_STOP}:
            return None
        return HookResponse(stdout={"decision": "block", "reason": reason}, applied_action=applied)
    if action == DecisionAction.REANCHOR:
        if event.event_type in {EventType.AGENT_STOP, EventType.SUBAGENT_STOP}:
            return HookResponse(
                stdout={"decision": "block", "reason": reason}, applied_action=applied
            )
        if event.event_type == EventType.SESSION_END:
            return None
        return HookResponse(
            stdout={
                "hookSpecificOutput": {
                    "hookEventName": hook_name,
                    "additionalContext": reason,
                }
            },
            applied_action=applied,
        )
    if action == DecisionAction.BLOCK:
        if event.event_type == EventType.TOOL_BEFORE:
            return HookResponse(
                stdout={
                    "hookSpecificOutput": {
                        "hookEventName": hook_name,
                        "permissionDecision": "deny",
                        "permissionDecisionReason": decision.reason,
                    }
                },
                applied_action=applied,
            )
        if event.event_type in {
            EventType.PROMPT_SUBMIT,
            EventType.TOOL_AFTER,
            EventType.TOOL_ERROR,
            EventType.AGENT_STOP,
            EventType.SUBAGENT_STOP,
        }:
            return HookResponse(
                stdout={"decision": "block", "reason": reason}, applied_action=applied
            )
        if event.event_type == EventType.COMPACTION_BEFORE:
            output = (
                {"continue": False, "stopReason": reason}
                if precompact_uses_continue
                else {"decision": "block", "reason": reason}
            )
            return HookResponse(stdout=output, applied_action=applied)
        return None
    if action == DecisionAction.ABORT:
        if event.event_type in {
            EventType.SESSION_START,
            EventType.PROMPT_SUBMIT,
            EventType.COMPACTION_BEFORE,
            EventType.COMPACTION_AFTER,
            EventType.TOOL_AFTER,
            EventType.TOOL_ERROR,
            EventType.AGENT_STOP,
            EventType.SUBAGENT_STOP,
        }:
            return HookResponse(
                stdout={"continue": False, "stopReason": decision.reason},
                applied_action=applied,
            )
        return None
    return None
