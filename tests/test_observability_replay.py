from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from agent_drift import (
    AgentDriftRuntime,
    CodexAdapter,
    GuardAnchors,
    JsonlExporter,
    ObservationEnvelope,
    SQLiteStore,
    Supervisor,
    TaskAnchor,
    export_store_session,
    load_replay_cases,
    run_replay,
)

NOW = datetime(2026, 8, 9, tzinfo=UTC)


def native_event(index: int) -> dict[str, object]:
    return {
        "session_id": "real-long-session",
        "turn_id": f"turn-{index}",
        "cwd": "/project",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_use_id": f"tool-{index}",
        "tool_input": {"command": f"pytest tests/test_{index}.py"},
    }


def test_jsonl_observations_round_trip_into_deterministic_replay(tmp_path: Path) -> None:
    observations = tmp_path / "observations.jsonl"
    observations.touch(mode=0o644)
    anchors = GuardAnchors(task=TaskAnchor(goal="Implement and validate the requested change."))
    runtime = AgentDriftRuntime(
        CodexAdapter(),
        Supervisor(anchors, store=SQLiteStore(tmp_path / "drift.db")),
        exporter=JsonlExporter(observations),
    )
    for index in range(20):
        outcome = runtime.handle(native_event(index), timestamp=NOW, repo_root="/project")
        assert outcome.export_error is None
        assert outcome.processing_duration_ms >= 0

    lines = observations.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 20
    first = ObservationEnvelope.model_validate_json(lines[0])
    assert first.supervision.event.session_id == "real-long-session"
    if os.name != "nt":
        assert observations.stat().st_mode & 0o777 == 0o600

    cases = load_replay_cases(observations)
    first_report = run_replay(cases, anchors, source=str(observations))
    second_report = run_replay(cases, anchors, source=str(observations))
    assert first_report.total_events == 20
    assert first_report.sessions == 1
    assert first_report.compared_events == 20
    assert first_report.mismatches == 0
    assert first_report.semantic_fingerprint == second_report.semantic_fingerprint


def test_replay_reports_expected_decision_mismatch(tmp_path: Path) -> None:
    anchors = GuardAnchors(task=TaskAnchor(goal="Stay in scope."))
    runtime = AgentDriftRuntime(CodexAdapter(), Supervisor(anchors))
    event = runtime.handle(native_event(0), timestamp=NOW).supervision.event
    path = tmp_path / "mismatch.jsonl"
    path.write_text(
        json.dumps(
            {
                "event": event.model_dump(mode="json"),
                "expected_action": "block",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    report = run_replay(load_replay_cases(path), anchors, source=str(path))
    assert report.compared_events == 1
    assert report.mismatches == 1
    assert report.entries[0].matches_expected is False


def test_sqlite_session_exports_private_replay_cases(tmp_path: Path) -> None:
    anchors = GuardAnchors(task=TaskAnchor(goal="Implement and validate."))
    store = SQLiteStore(tmp_path / "drift.db")
    runtime = AgentDriftRuntime(CodexAdapter(), Supervisor(anchors, store=store))
    for index in range(3):
        runtime.handle(native_event(index), timestamp=NOW)
    output = tmp_path / "exports" / "session.jsonl"
    output.parent.mkdir()
    output.touch(mode=0o644)
    result = export_store_session(store, "real-long-session", output)
    assert result.events == 3
    assert result.expected_decisions == 3
    assert len(load_replay_cases(output)) == 3
    if os.name != "nt":
        assert output.stat().st_mode & 0o777 == 0o600


def test_export_failure_does_not_change_guard_decision() -> None:
    class FailingExporter:
        def export(self, observation: ObservationEnvelope) -> None:
            raise OSError("disk unavailable")

    anchors = GuardAnchors(task=TaskAnchor(goal="Stay in scope."))
    runtime = AgentDriftRuntime(
        CodexAdapter(),
        Supervisor(anchors),
        exporter=FailingExporter(),
    )
    outcome = runtime.handle(native_event(0), timestamp=NOW)
    assert outcome.response.applied_action == "allow"
    assert outcome.export_error == "disk unavailable"
