from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_drift import (
    AgentDriftRuntime,
    ClaudeCodeAdapter,
    GuardAnchors,
    Supervisor,
    TaskAnchor,
)
from agent_drift.protocol.decisions import DecisionAction, DriftType
from agent_drift.store import SQLiteStore

NOW = datetime(2026, 8, 8, tzinfo=UTC)


def hook(event: str, **values: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "session_id": "durable-session",
        "prompt_id": "turn-1",
        "cwd": "/project",
        "hook_event_name": event,
    }
    document.update(values)
    return document


def runtime(path: Path) -> AgentDriftRuntime:
    anchors = GuardAnchors(task=TaskAnchor(goal="Implement and validate the feature."))
    return AgentDriftRuntime(
        ClaudeCodeAdapter(),
        Supervisor(anchors, store=SQLiteStore(path)),
    )


def test_new_runtime_process_recovers_prior_session_history(tmp_path: Path) -> None:
    path = tmp_path / "drift.db"
    first_process = runtime(path)
    write = first_process.handle(
        hook(
            "PreToolUse",
            tool_name="Write",
            tool_use_id="write-1",
            tool_input={"file_path": "/project/src/app.py", "content": "changed"},
        ),
        timestamp=NOW,
        repo_root="/project",
    )
    assert write.supervision.event.sequence == 0

    second_process = runtime(path)
    stop = second_process.handle(
        hook(
            "Stop",
            stop_hook_active=False,
            last_assistant_message="Implementation updated.",
            background_tasks=[],
            session_crons=[],
        ),
        timestamp=NOW,
        repo_root="/project",
    )
    assert stop.supervision.event.sequence == 1
    assert stop.supervision.decision.action == DecisionAction.CONTINUE
    assert {item.drift_type for item in stop.supervision.evidence} == {DriftType.VALIDATION}
    assert stop.response.stdout is not None
    assert stop.response.stdout["decision"] == "block"


def test_reprocessing_same_event_returns_persisted_result(tmp_path: Path) -> None:
    path = tmp_path / "drift.db"
    anchors = GuardAnchors(task=TaskAnchor(goal="Implement and validate the feature."))
    first_supervisor = Supervisor(anchors, store=SQLiteStore(path))
    native = hook(
        "PreToolUse",
        tool_name="Bash",
        tool_use_id="tool-1",
        tool_input={"command": "echo ok"},
    )
    event = ClaudeCodeAdapter().adapt_event(native, timestamp=NOW, repo_root="/project")
    first = first_supervisor.process(event)
    second = Supervisor(anchors, store=SQLiteStore(path)).process(event)
    assert second == first
    assert SQLiteStore(path).stats().events == 1
