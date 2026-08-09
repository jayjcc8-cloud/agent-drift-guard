from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, cast

from agent_drift import GuardAnchors, load_replay_cases, run_replay
from agent_drift.cli import main

PROJECT_ROOT = Path(__file__).parents[1]
CORPUS_ROOT = Path(__file__).parent / "fixtures" / "replay" / "v0.7"
PLATFORM_SCENARIOS = {
    "codex": {"clean", "loop", "validation", "lifecycle"},
    "claude-code": {"clean", "loop", "validation", "lifecycle"},
}
SAFE_COMMANDS = {
    "python3 -m unittest tests.test_calc",
    "python3 -m unittest tests.test_always_fail",
}
SECRET_VALUE_PATTERNS = (
    re.compile(r"\b(?:sk|ghp|github_pat|xox[baprs])[-_][A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r'(?i)"(?:api[_-]?key|auth[_-]?token|access[_-]?token)"\s*:\s*"[^"\s]+"'),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{12,}"),
)
HOME_PATH_PATTERNS = (
    re.compile(r"/Users/"),
    re.compile(r"/home/[^/]+/"),
    re.compile(r"[A-Za-z]:\\Users\\"),
)


def case_directories() -> tuple[Path, ...]:
    return tuple(sorted(path for path in CORPUS_ROOT.iterdir() if path.is_dir()))


def read_object(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def replay_report(directory: Path) -> Any:
    anchors = GuardAnchors.model_validate_json(
        (directory / "anchors.json").read_text(encoding="utf-8")
    )
    replay = directory / "replay.jsonl"
    source = replay.relative_to(PROJECT_ROOT).as_posix()
    return run_replay(load_replay_cases(replay), anchors, source=source)


def test_corpus_has_eight_real_reviewed_platform_scenarios() -> None:
    directories = case_directories()
    assert len(directories) == 8
    scenarios: dict[str, set[str]] = defaultdict(set)
    total_events = 0

    for directory in directories:
        expected_files = {
            "anchors.json",
            "replay.jsonl",
            "provenance.json",
            "expected-report.json",
        }
        assert {path.name for path in directory.iterdir()} == expected_files
        provenance = read_object(directory / "provenance.json")
        assert provenance["case_id"] == directory.name
        assert provenance["corpus_version"] == "v0.7"
        assert provenance["source_kind"] == "real-controlled-session"
        assert provenance["human_labels_reviewed"] is True
        assert re.fullmatch(r"[0-9a-f]{64}", provenance["raw_capture_sha256"])
        assert provenance["privacy_review"] == {
            "status": "passed",
            "raw_data_committed": False,
            "prompts_removed": True,
            "tool_outputs_removed": True,
            "transcript_paths_removed": True,
            "credentials_found": False,
            "reviewed_on": "2026-08-09",
        }
        cases = load_replay_cases(directory / "replay.jsonl")
        assert 4 <= len(cases) <= 6
        assert provenance["event_count"] == len(cases)
        assert all(case.expected_drift_types is not None for case in cases)
        assert all(case.expected_action is not None for case in cases)
        assert all(case.expected_semantic_fingerprint is not None for case in cases)
        total_events += len(cases)
        scenarios[provenance["platform"]].add(provenance["scenario"])

    assert dict(scenarios) == PLATFORM_SCENARIOS
    assert 32 <= total_events <= 48
    assert total_events == 40


def test_corpus_privacy_and_minimization_gate() -> None:
    all_text = "\n".join(
        path.read_text(encoding="utf-8") for path in CORPUS_ROOT.rglob("*") if path.is_file()
    )
    assert "[REDACTED]" not in all_text
    for pattern in (*SECRET_VALUE_PATTERNS, *HOME_PATH_PATTERNS):
        assert pattern.search(all_text) is None

    for directory in case_directories():
        for case in load_replay_cases(directory / "replay.jsonl"):
            event = case.event
            assert event.repo_root == "/workspace/fixture"
            assert event.cwd == "/workspace/fixture"
            assert not event.extensions
            assert "prompt" not in event.payload
            assert "result" not in event.payload
            payload_text = json.dumps(event.payload, ensure_ascii=False)
            assert "transcript" not in payload_text.lower()
            assert ".agent-drift" not in payload_text
            paths = event.payload.get("paths", [])
            assert isinstance(paths, list)
            assert all(
                isinstance(path, str) and path.startswith("/workspace/fixture/") for path in paths
            )
            arguments = event.payload.get("arguments")
            if isinstance(arguments, dict) and "command" in arguments:
                assert arguments["command"] in SAFE_COMMANDS


def test_each_case_replays_twice_with_exact_golden_results() -> None:
    aggregate = Counter[str]()
    total_labeled = 0
    total_exact = 0

    for directory in case_directories():
        first = replay_report(directory)
        second = replay_report(directory)
        expected = read_object(directory / "expected-report.json")
        provenance = read_object(directory / "provenance.json")

        assert first.semantic_fingerprint == second.semantic_fingerprint
        assert first.model_dump(mode="json", exclude_none=True) == expected
        assert first.mismatches == 0
        assert first.semantic_mismatches == 0
        assert first.quality.label_mismatches == 0
        assert first.quality.exact_match_rate == 1.0
        assert first.quality.clean_false_positive_rate == 0.0
        assert first.quality.false_negatives == 0
        assert first.decision_counts == provenance["expected_decision_counts"]
        assert first.evidence_counts == provenance["expected_drift_event_counts"]

        total_labeled += first.quality.labeled_events
        total_exact += first.quality.exact_matches
        aggregate["true_positives"] += first.quality.true_positives
        aggregate["false_positives"] += first.quality.false_positives
        aggregate["false_negatives"] += first.quality.false_negatives
        aggregate["clean_events"] += first.quality.clean_events
        aggregate["clean_false_positive_events"] += first.quality.clean_false_positive_events

    assert total_labeled == 40
    assert total_exact == 40
    assert aggregate == {
        "true_positives": 6,
        "false_positives": 0,
        "false_negatives": 0,
        "clean_events": 36,
        "clean_false_positive_events": 0,
    }


def test_codex_and_claude_scenario_distributions_are_equivalent() -> None:
    by_scenario: dict[str, dict[str, Any]] = defaultdict(dict)
    for directory in case_directories():
        provenance = read_object(directory / "provenance.json")
        by_scenario[provenance["scenario"]][provenance["platform"]] = (
            replay_report(directory),
            provenance,
        )

    for scenario, platforms in by_scenario.items():
        assert set(platforms) == {"codex", "claude-code"}
        codex_report, codex_provenance = platforms["codex"]
        claude_report, claude_provenance = platforms["claude-code"]
        assert codex_report.decision_counts == claude_report.decision_counts
        assert codex_report.evidence_counts == claude_report.evidence_counts
        assert codex_report.quality.exact_match_rate == claude_report.quality.exact_match_rate
        assert codex_report.quality.false_negatives == claude_report.quality.false_negatives
        if scenario == "validation":
            assert codex_provenance["documented_platform_differences"]
            assert claude_provenance["documented_platform_differences"]


def test_public_corpus_passes_cli_mismatch_gate(capsys: Any) -> None:
    for directory in case_directories():
        assert (
            main(
                [
                    "replay",
                    str(directory / "replay.jsonl"),
                    "--anchors",
                    str(directory / "anchors.json"),
                    "--summary-only",
                    "--fail-on-mismatch",
                ]
            )
            == 0
        )
    capsys.readouterr()
