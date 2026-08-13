from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from agent_drift.adapters import ClaudeCodeAdapter, CodexAdapter
from agent_drift.core import ConstraintAnchor, GuardAnchors, Supervisor, TaskAnchor
from agent_drift.protocol.decisions import DecisionAction, DriftType

NOW = datetime(2026, 8, 8, tzinfo=UTC)


def anchors(**constraint_overrides: Any) -> GuardAnchors:
    return GuardAnchors(
        task=TaskAnchor(
            goal="Implement authentication milestone M3.",
            acceptance_criteria=("All tests pass.",),
        ),
        constraints=ConstraintAnchor(**constraint_overrides),
    )


def codex_hook(event: str, **values: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "session_id": "s1",
        "turn_id": "t1",
        "cwd": "/project",
        "hook_event_name": event,
    }
    document.update(values)
    return document


def claude_hook(event: str, **values: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "session_id": "s1",
        "prompt_id": "t1",
        "cwd": "/project",
        "hook_event_name": event,
    }
    document.update(values)
    return document


def test_scope_detector_blocks_both_platforms_identically() -> None:
    supervisor = Supervisor(anchors(allowed_write_paths=("src/**", "tests/**")))
    for adapter, hook in (
        (CodexAdapter(), codex_hook),
        (ClaudeCodeAdapter(), claude_hook),
    ):
        if isinstance(adapter, CodexAdapter):
            native = hook(
                "PreToolUse",
                tool_name="apply_patch",
                tool_use_id="tool-patch",
                tool_input={"command": "*** Update File: infra/prod.tf\n@@"},
            )
        else:
            native = hook(
                "PreToolUse",
                tool_name="Edit",
                tool_use_id="tool-edit",
                tool_input={"file_path": "/project/infra/prod.tf", "old_string": "a"},
            )
        event = adapter.adapt_event(native, timestamp=NOW, repo_root="/project")
        result = supervisor.process(event)
        assert result.decision.action == DecisionAction.BLOCK
        assert result.evidence[0].drift_type == DriftType.SCOPE


def test_explicit_forbidden_command_is_critical_and_blocked() -> None:
    supervisor = Supervisor(anchors(forbidden_command_patterns=(r"rm\s+-rf",)))
    event = CodexAdapter().adapt_event(
        codex_hook(
            "PreToolUse",
            tool_name="Bash",
            tool_use_id="tool-1",
            tool_input={"command": "rm -rf build"},
        ),
        timestamp=NOW,
    )
    result = supervisor.process(event)
    assert result.decision.action == DecisionAction.BLOCK
    assert result.evidence[0].drift_type == DriftType.CONSTRAINT


def test_fifth_identical_tool_call_is_loop_drift() -> None:
    supervisor = Supervisor(anchors())
    adapter = CodexAdapter()
    for index in range(5):
        event = adapter.adapt_event(
            codex_hook(
                "PreToolUse",
                tool_name="Bash",
                tool_use_id=f"tool-{index}",
                tool_input={"command": "pytest tests/test_auth.py"},
            ),
            timestamp=NOW,
        )
        result = supervisor.process(event)
    assert result.decision.action == DecisionAction.BLOCK
    assert result.evidence[0].drift_type == DriftType.LOOP


def test_claude_display_description_does_not_hide_identical_loop() -> None:
    supervisor = Supervisor(anchors())
    adapter = ClaudeCodeAdapter()
    for index in range(5):
        event = adapter.adapt_event(
            claude_hook(
                "PreToolUse",
                tool_name="Bash",
                tool_use_id=f"tool-{index}",
                tool_input={
                    "command": "python3 -m unittest tests.test_always_fail",
                    "description": f"Run controlled failure (attempt {index + 1} of 5)",
                },
            ),
            timestamp=NOW,
        )
        result = supervisor.process(event)
    assert result.decision.action == DecisionAction.BLOCK
    assert result.evidence[0].drift_type == DriftType.LOOP


def _write_event(adapter: ClaudeCodeAdapter) -> Any:
    return adapter.adapt_event(
        claude_hook(
            "PreToolUse",
            tool_name="Write",
            tool_use_id="write-1",
            tool_input={"file_path": "/project/src/auth.py", "content": "changed"},
        ),
        timestamp=NOW,
        repo_root="/project",
    )


def _validation_event(adapter: ClaudeCodeAdapter, exit_code: int) -> Any:
    return adapter.adapt_event(
        claude_hook(
            "PostToolUse",
            tool_name="Bash",
            tool_use_id="test-1",
            tool_input={"command": "pytest"},
            tool_response={"exit_code": exit_code, "stdout": "result"},
        ),
        timestamp=NOW,
        repo_root="/project",
    )


def _stop_event(adapter: ClaudeCodeAdapter, message: str) -> Any:
    return adapter.adapt_event(
        claude_hook(
            "Stop",
            stop_hook_active=False,
            last_assistant_message=message,
            background_tasks=[],
            session_crons=[],
        ),
        timestamp=NOW,
        repo_root="/project",
    )


def test_stop_after_write_without_validation_continues_agent() -> None:
    supervisor = Supervisor(anchors())
    adapter = ClaudeCodeAdapter()
    supervisor.process(_write_event(adapter))
    result = supervisor.process(_stop_event(adapter, "Implementation updated."))
    assert result.decision.action == DecisionAction.CONTINUE
    assert {item.drift_type for item in result.evidence} == {DriftType.VALIDATION}


def test_successful_validation_allows_stop() -> None:
    supervisor = Supervisor(anchors())
    adapter = ClaudeCodeAdapter()
    supervisor.process(_write_event(adapter))
    supervisor.process(_validation_event(adapter, 0))
    result = supervisor.process(_stop_event(adapter, "Implementation complete."))
    assert result.decision.action == DecisionAction.ALLOW
    assert not result.evidence


def test_real_codex_unittest_success_allows_stop_with_default_repo_anchors() -> None:
    supervisor = Supervisor(anchors())
    adapter = CodexAdapter()
    supervisor.process(
        adapter.adapt_event(
            codex_hook(
                "PreToolUse",
                tool_name="apply_patch",
                tool_use_id="patch-1",
                tool_input={"command": "*** Update File: calc.py\n@@"},
            ),
            timestamp=NOW,
            repo_root="/project",
        )
    )
    supervisor.process(
        adapter.adapt_event(
            codex_hook(
                "PostToolUse",
                tool_name="Bash",
                tool_use_id="test-1",
                tool_input={"command": "python3 -m unittest tests.test_calc"},
                tool_response=(
                    ".\n----------------------------------------------------------------------\n"
                    "Ran 1 test in 0.000s\n\nOK\n"
                ),
            ),
            timestamp=NOW,
            repo_root="/project",
        )
    )
    supervisor.process(
        adapter.adapt_event(
            codex_hook(
                "PostToolUse",
                tool_name="Bash",
                tool_use_id="checkpoint-1",
                tool_input={
                    "command": (
                        "python3 work_unit.py checkpoint "
                        '--summary "python3 -m unittest tests.test_calc passes"'
                    )
                },
                tool_response='{"status":"closed"}',
            ),
            timestamp=NOW,
            repo_root="/project",
        )
    )

    result = supervisor.process(
        adapter.adapt_event(
            codex_hook(
                "Stop",
                stop_hook_active=False,
                last_assistant_message="Implementation completed.",
            ),
            timestamp=NOW,
            repo_root="/project",
        )
    )

    assert result.decision.action == DecisionAction.ALLOW
    assert not result.evidence


def test_completion_claim_after_failed_validation_is_state_and_validation_drift() -> None:
    supervisor = Supervisor(anchors())
    adapter = ClaudeCodeAdapter()
    supervisor.process(_write_event(adapter))
    supervisor.process(_validation_event(adapter, 1))
    result = supervisor.process(_stop_event(adapter, "Implementation complete. All done."))
    assert result.decision.action == DecisionAction.CONTINUE
    assert {item.drift_type for item in result.evidence} == {
        DriftType.STATE,
        DriftType.VALIDATION,
    }
    native = adapter.render_decision(result.event, result.decision)
    assert native.stdout is not None
    assert native.stdout["decision"] == "block"


def test_latest_validation_failure_overrides_earlier_success() -> None:
    supervisor = Supervisor(anchors())
    adapter = ClaudeCodeAdapter()
    supervisor.process(_write_event(adapter))
    supervisor.process(_validation_event(adapter, 0))
    supervisor.process(_validation_event(adapter, 1))
    result = supervisor.process(_stop_event(adapter, "Implementation updated."))
    assert result.decision.action == DecisionAction.CONTINUE
    assert {item.drift_type for item in result.evidence} == {DriftType.VALIDATION}


def test_fifth_consecutive_failure_reanchors() -> None:
    supervisor = Supervisor(anchors())
    adapter = ClaudeCodeAdapter()
    for _ in range(5):
        result = supervisor.process(_validation_event(adapter, 1))
    assert result.decision.action == DecisionAction.REANCHOR
    assert result.evidence[0].drift_type == DriftType.LOOP


def test_supervisor_assigns_monotonic_sequence_and_bounds_history() -> None:
    supervisor = Supervisor(anchors(), history_limit=2)
    adapter = CodexAdapter()
    sequences = []
    for index in range(3):
        result = supervisor.process(
            adapter.adapt_event(
                codex_hook(
                    "PreToolUse",
                    tool_name="Bash",
                    tool_use_id=f"tool-{index}",
                    tool_input={"command": f"echo {index}"},
                ),
                timestamp=NOW,
            )
        )
        sequences.append(result.event.sequence)
    assert sequences == [0, 1, 2]
    assert len(supervisor.history("s1")) == 2
