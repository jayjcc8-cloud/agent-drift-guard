from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agent_drift.installer import HookInstaller, HookInstallError


def executable(tmp_path: Path) -> Path:
    path = tmp_path / "bin with spaces" / "agent-drift"
    path.parent.mkdir()
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o700)
    return path


def test_codex_install_is_idempotent_and_preserves_existing_hooks(tmp_path: Path) -> None:
    project = tmp_path / "project with spaces"
    config = project / ".codex/hooks.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "description": "Existing project hooks",
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [{"type": "command", "command": "echo existing"}],
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    installer = HookInstaller("codex", project, executable=executable(tmp_path))
    result = installer.install()
    assert result.changed is True
    assert len(result.installed_events) == 10
    assert result.backup_path is not None
    assert Path(result.backup_path).exists()
    document = json.loads(config.read_text(encoding="utf-8"))
    pre_tool_groups = document["hooks"]["PreToolUse"]
    assert len(pre_tool_groups) == 2
    assert pre_tool_groups[0]["hooks"][0]["command"] == "echo existing"
    command = pre_tool_groups[1]["hooks"][0]["command"]
    assert "AGENT_DRIFT_GUARD=1" in command
    assert "'" in command  # paths containing spaces are shell quoted
    assert (project / ".agent-drift/anchors.json").exists()
    assert ".agent-drift/" in (project / ".gitignore").read_text(encoding="utf-8")
    assert result.gitignore_updated is True
    if os.name != "nt":
        assert config.stat().st_mode & 0o777 == 0o600

    second = installer.install()
    assert second.changed is False
    assert installer.status().installed_events == tuple(sorted(result.installed_events))

    removed = installer.uninstall()
    assert removed.changed is True
    remaining = json.loads(config.read_text(encoding="utf-8"))
    assert remaining["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "echo existing"
    assert installer.status().installed_events == ()


def test_claude_install_preserves_non_hook_settings(tmp_path: Path) -> None:
    project = tmp_path / "project"
    config = project / ".claude/settings.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"permissions": {"allow": ["Read"]}}), encoding="utf-8")
    installer = HookInstaller("claude-code", project, executable=executable(tmp_path))
    result = installer.install()
    assert len(result.installed_events) == 11
    document = json.loads(config.read_text(encoding="utf-8"))
    assert document["permissions"] == {"allow": ["Read"]}
    assert "PostToolUseFailure" in document["hooks"]


def test_install_dry_run_does_not_touch_project(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    installer = HookInstaller("codex", project, executable=executable(tmp_path))
    result = installer.install(dry_run=True)
    assert result.changed is True
    assert not installer.config_path.exists()
    assert not installer.data_root.exists()


@pytest.mark.skipif(os.name == "nt", reason="symlink behavior differs on Windows")
def test_installer_rejects_config_symlink_outside_project(tmp_path: Path) -> None:
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    (project / ".codex").symlink_to(outside, target_is_directory=True)
    with pytest.raises(HookInstallError, match="outside project root"):
        HookInstaller("codex", project, executable=executable(tmp_path))
