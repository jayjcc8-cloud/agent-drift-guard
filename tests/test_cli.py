import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agent_drift.cli import main
from agent_drift.protocol.events import AgentEvent, EventType
from agent_drift.store import SQLiteStore


class CliTests(unittest.TestCase):
    @staticmethod
    def _initialize_git(project: Path) -> None:
        subprocess.run(
            ["git", "init", "--quiet", str(project)],
            check=True,
            capture_output=True,
            text=True,
        )

    def _run_with_document(self, command: str, document: dict[str, object]) -> tuple[int, str, str]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main([command, str(path)])
            return code, stdout.getvalue(), stderr.getvalue()

    def test_validate_event(self) -> None:
        code, stdout, stderr = self._run_with_document(
            "validate-event",
            {
                "event_type": "session.start",
                "platform": "test",
                "session_id": "s1",
                "timestamp": "2026-08-08T08:00:00Z",
            },
        )
        self.assertEqual(code, 0)
        self.assertFalse(stderr)
        self.assertEqual(json.loads(stdout)["protocol_version"], "0.2")

    def test_invalid_event_returns_two(self) -> None:
        code, _, stderr = self._run_with_document("validate-event", {"event_type": "session.start"})
        self.assertEqual(code, 2)
        self.assertIn("agent-drift:", stderr)

    def test_adapt_native_codex_hook(self) -> None:
        document = {
            "session_id": "s1",
            "turn_id": "t1",
            "cwd": "/project",
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "pytest"},
            "tool_use_id": "tool-1",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hook.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(["adapt-hook", "codex", str(path), "--repo-root", "/project"])
        self.assertEqual(code, 0)
        self.assertFalse(stderr.getvalue())
        event = json.loads(stdout.getvalue())
        self.assertEqual(event["event_type"], "tool.before")
        self.assertEqual(event["payload"]["tool"], "shell")

    def test_adapter_capabilities(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["adapter-capabilities", "codex", "--platform-version", "test"])
        self.assertEqual(code, 0)
        self.assertFalse(stderr.getvalue())
        document = json.loads(stdout.getvalue())
        self.assertEqual(document["platform"], "codex")
        self.assertEqual(document["protection_level"], "full")

    def test_durable_hook_cli_recovers_history_between_invocations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "drift.db"
            anchors = root / "anchors.json"
            write_hook = root / "write.json"
            stop_hook = root / "stop.json"
            anchors.write_text(
                json.dumps({"task": {"goal": "Implement and validate."}}),
                encoding="utf-8",
            )
            write_hook.write_text(
                json.dumps(
                    {
                        "session_id": "s1",
                        "prompt_id": "t1",
                        "cwd": "/project",
                        "hook_event_name": "PreToolUse",
                        "tool_name": "Write",
                        "tool_use_id": "write-1",
                        "tool_input": {
                            "file_path": "/project/src/app.py",
                            "content": "changed",
                        },
                    }
                ),
                encoding="utf-8",
            )
            stop_hook.write_text(
                json.dumps(
                    {
                        "session_id": "s1",
                        "prompt_id": "t1",
                        "cwd": "/project",
                        "hook_event_name": "Stop",
                        "stop_hook_active": False,
                        "last_assistant_message": "Implementation updated.",
                        "background_tasks": [],
                        "session_crons": [],
                    }
                ),
                encoding="utf-8",
            )
            common = [
                "--database",
                str(database),
                "--anchors",
                str(anchors),
                "--repo-root",
                "/project",
            ]
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                first_code = main(["hook", "claude-code", str(write_hook), *common])
            self.assertEqual(first_code, 0)
            self.assertEqual(stdout.getvalue(), "")

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                second_code = main(["hook", "claude-code", str(stop_hook), *common])
            self.assertEqual(second_code, 0)
            self.assertEqual(json.loads(stdout.getvalue())["decision"], "block")

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                stats_code = main(["store-stats", str(database)])
            self.assertEqual(stats_code, 0)
            stats = json.loads(stdout.getvalue())
            self.assertEqual(stats["events"], 2)
            self.assertEqual(stats["evidence"], 1)
            self.assertEqual(stats["integrity"], "ok")

    def test_store_prune_requires_apply_to_delete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "drift.db"
            store = SQLiteStore(database, retention_policy=None)
            for index in range(2):
                store.prepare_event(
                    AgentEvent(
                        event_type=EventType.TOOL_BEFORE,
                        platform="test",
                        session_id="s1",
                        payload={"tool": "shell", "index": index},
                    )
                )
            with sqlite3.connect(database) as connection:
                connection.execute("UPDATE events SET stored_at_epoch = 0 WHERE sequence = 0")
            command = [
                "store-prune",
                str(database),
                "--no-age-limit",
                "--max-events-per-session",
                "1",
            ]
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                preview_code = main(command)
            self.assertEqual(preview_code, 0)
            self.assertEqual(json.loads(stdout.getvalue())["matched_events"], 1)
            self.assertEqual(store.stats().events, 2)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                apply_code = main([*command, "--apply"])
            self.assertEqual(apply_code, 0)
            self.assertEqual(json.loads(stdout.getvalue())["deleted_events"], 1)
            self.assertEqual(store.stats().events, 1)

    def test_benchmark_hook_runs_a_fresh_command_process(self) -> None:
        project = Path(__file__).resolve().parents[1]
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(
                [
                    "benchmark-hook",
                    "codex",
                    str(project / "tests/fixtures/codex/pre_tool_use.json"),
                    "--anchors",
                    str(project / "examples/anchors.json"),
                    "--iterations",
                    "1",
                    "--warmup",
                    "0",
                    "--budget-ms",
                    "10000",
                ]
            )
        self.assertEqual(code, 0)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["iterations"], 1)
        self.assertTrue(result["telemetry_enabled"])
        self.assertFalse(result["budget_exceeded"])
        self.assertGreater(result["latency"]["minimum_ms"], 0)

    def test_hook_telemetry_can_be_replayed_from_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = Path(__file__).resolve().parents[1]
            database = root / "drift.db"
            telemetry = root / "observations.jsonl"
            common = [
                "--database",
                str(database),
                "--anchors",
                str(project / "examples/anchors.json"),
                "--telemetry-jsonl",
                str(telemetry),
            ]
            with redirect_stdout(io.StringIO()):
                hook_code = main(
                    [
                        "hook",
                        "codex",
                        str(project / "tests/fixtures/codex/pre_tool_use.json"),
                        *common,
                    ]
                )
            self.assertEqual(hook_code, 0)
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                replay_code = main(
                    [
                        "replay",
                        str(telemetry),
                        "--anchors",
                        str(project / "examples/anchors.json"),
                        "--summary-only",
                        "--assume-clean",
                        "--fail-on-mismatch",
                    ]
                )
            self.assertEqual(replay_code, 0)
            report = json.loads(stdout.getvalue())
            self.assertEqual(report["total_events"], 1)
            self.assertEqual(report["mismatches"], 0)
            self.assertNotIn("entries", report)
            self.assertEqual(report["semantic_compared_events"], 1)
            self.assertEqual(report["quality"]["labeled_events"], 1)
            self.assertEqual(report["quality"]["label_mismatches"], 0)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                status_code = main(["telemetry-status", str(telemetry)])
            self.assertEqual(status_code, 0)
            telemetry_status = json.loads(stdout.getvalue())
            self.assertGreater(telemetry_status["current_bytes"], 0)
            self.assertEqual(telemetry_status["failure_count"], 0)

    def test_replay_fail_on_mismatch_includes_semantic_regressions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = Path(__file__).resolve().parents[1]
            replay = root / "semantic-mismatch.jsonl"
            replay.write_text(
                json.dumps(
                    {
                        "event": AgentEvent(
                            event_type=EventType.SESSION_START,
                            platform="test",
                            session_id="semantic-mismatch",
                        ).model_dump(mode="json"),
                        "expected_semantic_fingerprint": "0" * 64,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "replay",
                        str(replay),
                        "--anchors",
                        str(project / "examples/anchors.json"),
                        "--fail-on-mismatch",
                    ]
                )
            self.assertEqual(code, 1)

    @unittest.skipIf(os.name == "nt", "POSIX file modes are required")
    def test_replay_output_is_written_privately(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = Path(__file__).resolve().parents[1]
            output = root / "report.json"

            code = main(
                [
                    "replay",
                    str(project / "tests/fixtures/replay/v0.7/codex-clean/replay.jsonl"),
                    "--anchors",
                    str(project / "tests/fixtures/replay/v0.7/codex-clean/anchors.json"),
                    "--output",
                    str(output),
                    "--summary-only",
                ]
            )

            self.assertEqual(code, 0)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_install_status_and_uninstall_hooks_from_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            self._initialize_git(project)
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                install_code = main(
                    [
                        "install-hooks",
                        "codex",
                        "--project-root",
                        str(project),
                        "--executable",
                        sys.executable,
                    ]
                )
            self.assertEqual(install_code, 0)
            self.assertEqual(len(json.loads(stdout.getvalue())["installed_events"]), 11)

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                status_code = main(
                    [
                        "hook-status",
                        "codex",
                        "--project-root",
                        str(project),
                        "--executable",
                        sys.executable,
                    ]
                )
            self.assertEqual(status_code, 0)
            status = json.loads(stdout.getvalue())
            self.assertEqual(len(status["installed_events"]), 11)
            self.assertTrue(status["healthy"])

            with redirect_stdout(io.StringIO()):
                uninstall_code = main(
                    [
                        "uninstall-hooks",
                        "codex",
                        "--project-root",
                        str(project),
                        "--executable",
                        sys.executable,
                    ]
                )
            self.assertEqual(uninstall_code, 0)

    def test_hook_status_returns_one_for_missing_runner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            self._initialize_git(project)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(
                        [
                            "install-hooks",
                            "codex",
                            "--project-root",
                            str(project),
                            "--executable",
                            sys.executable,
                        ]
                    ),
                    0,
                )
            (project / ".agent-drift/codex-hook").unlink()

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = main(
                    [
                        "hook-status",
                        "codex",
                        "--project-root",
                        str(project),
                        "--executable",
                        sys.executable,
                    ]
                )

            self.assertEqual(code, 1)
            status = json.loads(stdout.getvalue())
            self.assertFalse(status["healthy"])
            self.assertIn("missing Hook runner", status["health_issues"])

    def test_install_uses_current_agent_drift_entrypoint_when_not_on_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            self._initialize_git(project)
            entrypoint = project / "bin" / "agent-drift"
            entrypoint.parent.mkdir()
            entrypoint.write_text("#!/bin/sh\n", encoding="utf-8")
            entrypoint.chmod(0o700)
            with patch.object(sys, "argv", [str(entrypoint)]), redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "install-hooks",
                        "codex",
                        "--project-root",
                        str(project),
                    ]
                )
            self.assertEqual(code, 0)
            document = json.loads((project / ".codex/hooks.json").read_text(encoding="utf-8"))
            command = document["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
            self.assertNotIn(str(entrypoint), command)
            runner = project / ".agent-drift/codex-hook"
            self.assertIn(str(entrypoint), runner.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
