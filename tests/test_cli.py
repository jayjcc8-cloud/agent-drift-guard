import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from agent_drift.cli import main
from agent_drift.protocol.events import AgentEvent, EventType
from agent_drift.store import SQLiteStore


class CliTests(unittest.TestCase):
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
        self.assertEqual(json.loads(stdout)["protocol_version"], "0.1")

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
            store = SQLiteStore(database)
            for index in range(2):
                store.prepare_event(
                    AgentEvent(
                        event_type=EventType.TOOL_BEFORE,
                        platform="test",
                        session_id="s1",
                        payload={"tool": "shell", "index": index},
                    )
                )
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
        self.assertFalse(result["budget_exceeded"])
        self.assertGreater(result["latency"]["minimum_ms"], 0)


if __name__ == "__main__":
    unittest.main()
