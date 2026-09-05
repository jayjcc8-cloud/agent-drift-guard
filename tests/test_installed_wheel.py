from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    return subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


def test_installed_wheel_observe_runner_is_non_intervening_outside_source(
    tmp_path: Path,
) -> None:
    source = Path(__file__).resolve().parents[1]
    wheelhouse = tmp_path / "wheelhouse"
    _run(["uv", "build", "--wheel", "--out-dir", str(wheelhouse)], cwd=source)
    wheel = next(wheelhouse.glob("agent_drift_guard-*.whl"))

    environment = tmp_path / "environment"
    _run(["uv", "venv", "--python", sys.executable, str(environment)], cwd=tmp_path)
    python = environment / "bin" / "python"
    _run(["uv", "pip", "install", "--python", str(python), str(wheel)], cwd=tmp_path)
    executable = environment / "bin" / "agent-drift"

    project = tmp_path / "project"
    project.mkdir()
    _run(["git", "init", "--quiet"], cwd=project)
    anchors = tmp_path / "anchors.json"
    anchors.write_text(
        json.dumps(
            {
                "task": {"goal": "Observe without intervening."},
                "constraints": {"forbidden_command_patterns": [r"rm\s+-rf"]},
            }
        ),
        encoding="utf-8",
    )

    installed = _run(
        [
            str(executable),
            "install-hooks",
            "codex",
            "--project-root",
            str(project),
            "--anchors",
            str(anchors),
        ],
        cwd=tmp_path,
    )
    assert json.loads(installed.stdout)["mode"] == "observe"

    config = json.loads((project / ".codex/hooks.json").read_text(encoding="utf-8"))
    handler = config["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert "--mode observe" in (project / ".agent-drift/codex-hook").read_text(encoding="utf-8")
    nested = project / "packages" / "app"
    nested.mkdir(parents=True)
    hook_input = json.dumps(
        {
            "session_id": "wheel-session",
            "turn_id": "turn-1",
            "agent_id": "main",
            "cwd": str(nested),
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_use_id": "forbidden-1",
            "tool_input": {"command": "rm -rf build"},
        }
    )
    observed = subprocess.run(
        handler,
        cwd=nested,
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
        input=hook_input,
        shell=True,
        executable="/bin/sh",
        check=False,
        capture_output=True,
        text=True,
    )

    assert observed.returncode == 0
    assert observed.stdout == ""
    assert observed.stderr == ""
    database = project / ".agent-drift/drift.db"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT action FROM decisions").fetchone() == ("block",)
        assert connection.execute("SELECT COUNT(*) FROM evidence").fetchone() == (1,)

    status = _run(
        [
            str(executable),
            "hook-status",
            "codex",
            "--project-root",
            str(project),
        ],
        cwd=tmp_path,
    )
    assert json.loads(status.stdout)["mode"] == "observe"

    removed = _run(
        [
            str(executable),
            "uninstall-hooks",
            "codex",
            "--project-root",
            str(project),
        ],
        cwd=tmp_path,
    )
    assert json.loads(removed.stdout)["installed_events"] == []
