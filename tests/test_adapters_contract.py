from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from agent_drift.adapters import (
    ClaudeCodeAdapter,
    CodexAdapter,
    UnsupportedDecisionError,
)
from agent_drift.protocol.capabilities import ProtectionLevel
from agent_drift.protocol.decisions import DecisionAction, GuardDecision
from agent_drift.protocol.events import AgentEvent, EventType

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 8, 8, tzinfo=UTC)


def load_fixture(platform: str, name: str) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads((FIXTURES / platform / name).read_text(encoding="utf-8")),
    )


def semantic_projection(event: AgentEvent) -> dict[str, Any]:
    return {
        "event_type": event.event_type,
        "session_id": event.session_id,
        "turn_id": event.turn_id,
        "agent_id": event.agent_id,
        "repo_root": event.repo_root,
        "cwd": event.cwd,
        "payload": event.payload,
    }


@pytest.mark.parametrize("fixture", ["pre_tool_use.json", "post_tool_use.json"])
def test_equivalent_native_hooks_produce_equivalent_core_events(fixture: str) -> None:
    codex = CodexAdapter().adapt_event(
        load_fixture("codex", fixture), timestamp=NOW, repo_root="/project"
    )
    claude = ClaudeCodeAdapter().adapt_event(
        load_fixture("claude", fixture), timestamp=NOW, repo_root="/project"
    )
    assert semantic_projection(codex) == semantic_projection(claude)


def test_permission_request_contract_is_equivalent_and_defers_when_allowed() -> None:
    codex_adapter = CodexAdapter()
    claude_adapter = ClaudeCodeAdapter()
    codex = codex_adapter.adapt_event(
        load_fixture("codex", "permission_request.json"), timestamp=NOW, repo_root="/project"
    )
    claude = claude_adapter.adapt_event(
        load_fixture("claude", "permission_request.json"), timestamp=NOW, repo_root="/project"
    )
    assert semantic_projection(codex) == semantic_projection(claude)
    assert codex.event_type == EventType.PERMISSION_REQUEST
    allow = GuardDecision(action=DecisionAction.ALLOW, reason="No drift detected.")
    assert codex_adapter.render_decision(codex, allow).applied_action == "defer"
    assert claude_adapter.render_decision(claude, allow).applied_action == "defer"


def test_permission_request_block_uses_native_permission_decision() -> None:
    decision = GuardDecision(action=DecisionAction.BLOCK, reason="Push is outside the task.")
    for adapter, platform in (
        (CodexAdapter(), "codex"),
        (ClaudeCodeAdapter(), "claude"),
    ):
        event = adapter.adapt_event(
            load_fixture(platform, "permission_request.json"), timestamp=NOW
        )
        response = adapter.render_decision(event, decision)
        assert response.stdout == {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {
                    "behavior": "deny",
                    "message": "Push is outside the task.",
                },
            }
        }


def test_claude_task_completed_and_stop_failure_follow_observation_contracts() -> None:
    adapter = ClaudeCodeAdapter()
    completed = adapter.adapt_event(load_fixture("claude", "task_completed.json"), timestamp=NOW)
    assert completed.event_type == EventType.TASK_COMPLETED
    response = adapter.render_decision(
        completed,
        GuardDecision(action=DecisionAction.CONTINUE, reason="Run validation first."),
    )
    assert response.exit_code == 2
    assert response.stderr == "Run validation first."

    failure = adapter.adapt_event(load_fixture("claude", "stop_failure.json"), timestamp=NOW)
    assert failure.event_type == EventType.AGENT_ERROR
    assert failure.payload["error"] == "rate_limit"
    assert (
        adapter.render_decision(
            failure, GuardDecision(action=DecisionAction.ALLOW, reason="Observation only.")
        ).stdout
        is None
    )


def test_real_platform_fields_stay_in_namespaced_extensions() -> None:
    event = CodexAdapter().adapt_event(load_fixture("codex", "pre_tool_use.json"), timestamp=NOW)
    assert event.extensions["codex.model"] == "gpt-example"
    assert event.extensions["codex.tool_name"] == "Bash"
    assert "model" not in event.payload


def test_claude_failure_maps_to_unified_tool_error() -> None:
    event = ClaudeCodeAdapter().adapt_event(
        load_fixture("claude", "post_tool_failure.json"), timestamp=NOW
    )
    assert event.event_type == EventType.TOOL_ERROR
    assert event.payload["outcome"] == "failure"


def test_codex_rejects_claude_only_failure_hook() -> None:
    with pytest.raises(ValueError, match="unsupported codex"):
        CodexAdapter().adapt_event(load_fixture("claude", "post_tool_failure.json"), timestamp=NOW)


def test_both_adapters_meet_full_baseline_with_documented_limitations() -> None:
    for adapter in (CodexAdapter(), ClaudeCodeAdapter()):
        assert adapter.capabilities.protection_level == ProtectionLevel.FULL
        assert adapter.capabilities.notes


def test_block_translation_is_equivalent_for_pre_tool_use() -> None:
    decision = GuardDecision(action=DecisionAction.BLOCK, reason="Outside allowed scope.")
    codex_adapter = CodexAdapter()
    claude_adapter = ClaudeCodeAdapter()
    codex_event = codex_adapter.adapt_event(
        load_fixture("codex", "pre_tool_use.json"), timestamp=NOW
    )
    claude_event = claude_adapter.adapt_event(
        load_fixture("claude", "pre_tool_use.json"), timestamp=NOW
    )
    codex = codex_adapter.render_decision(codex_event, decision)
    claude = claude_adapter.render_decision(claude_event, decision)
    assert codex.stdout == {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "Outside allowed scope.",
        }
    }
    assert claude.stdout == codex.stdout


def test_precompact_block_respects_native_contract_difference() -> None:
    decision = GuardDecision(action=DecisionAction.BLOCK, reason="Capture anchors first.")
    codex_adapter = CodexAdapter()
    claude_adapter = ClaudeCodeAdapter()
    codex_event = codex_adapter.adapt_event(
        load_fixture("codex", "pre_compact.json"), timestamp=NOW
    )
    claude_event = claude_adapter.adapt_event(
        load_fixture("claude", "pre_compact.json"), timestamp=NOW
    )
    assert codex_adapter.render_decision(codex_event, decision).stdout == {
        "continue": False,
        "stopReason": "Capture anchors first.",
    }
    assert claude_adapter.render_decision(claude_event, decision).stdout == {
        "decision": "block",
        "reason": "Capture anchors first.",
    }


def test_unsupported_retry_uses_only_explicit_fallback() -> None:
    adapter = CodexAdapter()
    event = adapter.adapt_event(load_fixture("codex", "pre_tool_use.json"), timestamp=NOW)
    with pytest.raises(UnsupportedDecisionError):
        adapter.render_decision(
            event,
            GuardDecision(
                action=DecisionAction.RETRY,
                reason="Try again.",
                max_retries=1,
            ),
        )
    response = adapter.render_decision(
        event,
        GuardDecision(
            action=DecisionAction.RETRY,
            fallback_action=DecisionAction.WARN,
            reason="Try again.",
            max_retries=1,
        ),
    )
    assert response.applied_action == "warn"
    assert response.stdout == {"systemMessage": "Try again."}


def test_stop_requires_native_boolean_guard_field() -> None:
    with pytest.raises(ValueError, match="boolean field 'stop_hook_active'"):
        ClaudeCodeAdapter().adapt_event(
            {
                "session_id": "s1",
                "cwd": "/project",
                "hook_event_name": "Stop",
                "stop_hook_active": "false",
                "last_assistant_message": None,
            },
            timestamp=NOW,
        )
