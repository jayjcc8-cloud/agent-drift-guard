from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from agent_drift import (
    AgentDriftRuntime,
    ClaudeCodeAdapter,
    CodexAdapter,
    ConstraintAnchor,
    DecisionAction,
    DriftType,
    GuardAnchors,
    Supervisor,
    TaskAnchor,
)
from agent_drift.cli import main

NOW = datetime(2026, 9, 5, tzinfo=UTC)


def _anchors(**constraints: Any) -> GuardAnchors:
    return GuardAnchors(
        task=TaskAnchor(goal="Implement and validate the authorized change."),
        constraints=ConstraintAnchor(**constraints),
    )


def _claude_hook(event: str, *, agent_id: str = "main", **values: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "session_id": "shared-session",
        "prompt_id": "turn-1",
        "agent_id": agent_id,
        "cwd": "/project",
        "hook_event_name": event,
    }
    document.update(values)
    return document


def _write(runtime: AgentDriftRuntime, *, agent_id: str = "main") -> None:
    runtime.handle(
        _claude_hook(
            "PreToolUse",
            agent_id=agent_id,
            tool_name="Write",
            tool_use_id=f"write-{agent_id}",
            tool_input={"file_path": "/project/src/app.py", "content": "changed"},
        ),
        timestamp=NOW,
        repo_root="/project",
    )


@pytest.mark.parametrize(
    ("adapter", "native"),
    [
        (
            CodexAdapter(),
            {
                "session_id": "s1",
                "turn_id": "t1",
                "agent_id": "main",
                "cwd": "/project",
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_use_id": "tool-1",
                "tool_input": {"command": "rm -rf build"},
            },
        ),
        (
            ClaudeCodeAdapter(),
            {
                "session_id": "s1",
                "prompt_id": "t1",
                "agent_id": "main",
                "cwd": "/project",
                "hook_event_name": "PermissionRequest",
                "tool_name": "Bash",
                "tool_use_id": "tool-1",
                "tool_input": {"command": "rm -rf build"},
                "permission_suggestions": [],
            },
        ),
    ],
)
def test_observe_keeps_proposed_block_without_native_permission_decision(
    adapter: CodexAdapter | ClaudeCodeAdapter,
    native: dict[str, Any],
) -> None:
    runtime = AgentDriftRuntime(
        adapter,
        Supervisor(_anchors(forbidden_command_patterns=(r"rm\s+-rf",))),
        mode="observe",
    )

    outcome = runtime.handle(native, timestamp=NOW, repo_root="/project")

    assert outcome.supervision.decision.action == DecisionAction.BLOCK
    assert {item.drift_type for item in outcome.supervision.evidence} == {DriftType.CONSTRAINT}
    assert outcome.response.stdout is None
    assert outcome.response.stderr == ""
    assert outcome.response.exit_code == 0
    assert outcome.response.applied_action == "observe"


@pytest.mark.parametrize(
    ("event", "extra"),
    [
        (
            "Stop",
            {
                "stop_hook_active": True,
                "last_assistant_message": "Validation failed; handing off incomplete work.",
                "background_tasks": [],
                "session_crons": [],
            },
        ),
        (
            "SubagentStop",
            {
                "stop_hook_active": True,
                "agent_type": "reviewer",
                "agent_transcript_path": "/tmp/subagent.jsonl",
                "last_assistant_message": "Validation failed; handing off incomplete work.",
                "background_tasks": [],
                "session_crons": [],
            },
        ),
        (
            "TaskCompleted",
            {"task_id": "task-1", "task_subject": "Authorized change"},
        ),
    ],
)
def test_observe_never_continues_stop_or_task_completion(event: str, extra: dict[str, Any]) -> None:
    runtime = AgentDriftRuntime(ClaudeCodeAdapter(), Supervisor(_anchors()), mode="observe")
    _write(runtime)

    outcome = runtime.handle(_claude_hook(event, **extra), timestamp=NOW, repo_root="/project")

    assert outcome.supervision.decision.action == DecisionAction.CONTINUE
    assert outcome.response.stdout is None
    assert outcome.response.stderr == ""
    assert outcome.response.exit_code == 0
    assert outcome.response.applied_action == "observe"


def test_runtime_default_remains_legacy_enforce_behavior() -> None:
    runtime = AgentDriftRuntime(
        CodexAdapter(),
        Supervisor(_anchors(forbidden_command_patterns=(r"rm\s+-rf",))),
    )
    outcome = runtime.handle(
        {
            "session_id": "s1",
            "turn_id": "t1",
            "agent_id": "main",
            "cwd": "/project",
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_use_id": "tool-1",
            "tool_input": {"command": "rm -rf build"},
        },
        timestamp=NOW,
    )
    assert outcome.response.applied_action == "block"
    assert outcome.response.stdout is not None


@pytest.mark.parametrize("failure", ["invalid-json", "missing-anchors", "bad-database"])
def test_observe_hook_errors_are_non_intervening_and_sanitized(
    tmp_path: Path, failure: str
) -> None:
    hook = tmp_path / "hook.json"
    anchors = tmp_path / "anchors.json"
    database = tmp_path / "drift.db"
    hook.write_text(
        "{secret-token"
        if failure == "invalid-json"
        else json.dumps(
            {
                "session_id": "s1",
                "turn_id": "t1",
                "agent_id": "main",
                "cwd": "/project",
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_use_id": "tool-1",
                "tool_input": {"command": "echo ok"},
            }
        ),
        encoding="utf-8",
    )
    if failure != "missing-anchors":
        anchors.write_text(json.dumps({"task": {"goal": "Observe."}}), encoding="utf-8")
    if failure == "bad-database":
        database.mkdir()

    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(
            [
                "hook",
                "codex",
                str(hook),
                "--mode",
                "observe",
                "--database",
                str(database),
                "--anchors",
                str(anchors),
            ]
        )

    assert code == 0
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "agent-drift: observation unavailable\n"
    assert "secret-token" not in stderr.getvalue()


def test_non_hook_validation_error_still_returns_two(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{not-json", encoding="utf-8")
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        assert main(["validate-event", str(invalid)]) == 2


def test_observe_export_failure_reports_only_sanitized_degradation(tmp_path: Path) -> None:
    hook = tmp_path / "hook.json"
    anchors = tmp_path / "anchors.json"
    hook.write_text(
        json.dumps(
            {
                "session_id": "s1",
                "turn_id": "t1",
                "agent_id": "main",
                "cwd": "/project",
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_use_id": "tool-1",
                "tool_input": {"command": "echo ok"},
            }
        ),
        encoding="utf-8",
    )
    anchors.write_text(json.dumps({"task": {"goal": "Observe."}}), encoding="utf-8")
    stdout = io.StringIO()
    stderr = io.StringIO()

    with (
        patch(
            "agent_drift.observability.JsonlExporter.export",
            side_effect=OSError("secret prompt and /private/path"),
        ),
        redirect_stdout(stdout),
        redirect_stderr(stderr),
    ):
        code = main(
            [
                "hook",
                "codex",
                str(hook),
                "--mode",
                "observe",
                "--database",
                str(tmp_path / "drift.db"),
                "--anchors",
                str(anchors),
                "--telemetry-jsonl",
                str(tmp_path / "observations.jsonl"),
            ]
        )

    assert code == 0
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "agent-drift: observation export failed\n"
