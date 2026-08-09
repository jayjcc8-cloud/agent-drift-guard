from datetime import UTC, datetime

from agent_drift import (
    AgentDriftRuntime,
    ClaudeCodeAdapter,
    CodexAdapter,
    ConstraintAnchor,
    GuardAnchors,
    Supervisor,
    TaskAnchor,
)

NOW = datetime(2026, 8, 8, tzinfo=UTC)


def test_codex_runtime_blocks_out_of_scope_patch_end_to_end() -> None:
    anchors = GuardAnchors(
        task=TaskAnchor(goal="Only edit application code."),
        constraints=ConstraintAnchor(allowed_write_paths=("src/**",)),
    )
    runtime = AgentDriftRuntime(CodexAdapter(), Supervisor(anchors))
    outcome = runtime.handle(
        {
            "session_id": "s1",
            "turn_id": "t1",
            "cwd": "/project",
            "hook_event_name": "PreToolUse",
            "tool_name": "apply_patch",
            "tool_use_id": "patch-1",
            "tool_input": {"command": "*** Update File: infra/prod.tf\n@@"},
        },
        timestamp=NOW,
        repo_root="/project",
    )
    assert outcome.response.applied_action == "block"
    assert outcome.response.stdout is not None
    hook_output = outcome.response.stdout["hookSpecificOutput"]
    assert isinstance(hook_output, dict)
    assert hook_output["permissionDecision"] == "deny"


def test_claude_runtime_preserves_history_across_hooks() -> None:
    anchors = GuardAnchors(task=TaskAnchor(goal="Implement feature and validate it."))
    runtime = AgentDriftRuntime(ClaudeCodeAdapter(), Supervisor(anchors))
    runtime.handle(
        {
            "session_id": "s1",
            "prompt_id": "t1",
            "cwd": "/project",
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_use_id": "write-1",
            "tool_input": {"file_path": "/project/src/app.py", "content": "changed"},
        },
        timestamp=NOW,
        repo_root="/project",
    )
    outcome = runtime.handle(
        {
            "session_id": "s1",
            "prompt_id": "t1",
            "cwd": "/project",
            "hook_event_name": "Stop",
            "stop_hook_active": False,
            "last_assistant_message": "Implementation updated.",
            "background_tasks": [],
            "session_crons": [],
        },
        timestamp=NOW,
        repo_root="/project",
    )
    assert outcome.response.applied_action == "continue"
    assert outcome.response.stdout is not None
    assert outcome.response.stdout["decision"] == "block"


def test_claude_task_completion_is_blocked_until_validation_succeeds() -> None:
    anchors = GuardAnchors(task=TaskAnchor(goal="Implement feature and validate it."))
    runtime = AgentDriftRuntime(ClaudeCodeAdapter(), Supervisor(anchors))
    runtime.handle(
        {
            "session_id": "s-task",
            "cwd": "/project",
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_use_id": "write-task",
            "tool_input": {"file_path": "/project/src/app.py", "content": "changed"},
        },
        timestamp=NOW,
        repo_root="/project",
    )
    outcome = runtime.handle(
        {
            "session_id": "s-task",
            "cwd": "/project",
            "hook_event_name": "TaskCompleted",
            "task_id": "task-1",
            "task_subject": "Implement feature",
        },
        timestamp=NOW,
        repo_root="/project",
    )
    assert outcome.supervision.decision.action.value == "continue"
    assert outcome.response.exit_code == 2
    assert "validation" in outcome.response.stderr.lower()
