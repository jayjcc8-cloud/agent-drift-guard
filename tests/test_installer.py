from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from agent_drift.installer import HookInstaller, HookInstallError
from agent_drift.store import SQLiteStore


def executable(tmp_path: Path) -> Path:
    path = tmp_path / "bin with spaces" / "agent-drift"
    path.parent.mkdir()
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o700)
    return path


def initialize_git(project: Path) -> None:
    project.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "--quiet", str(project)],
        check=True,
        capture_output=True,
        text=True,
    )


def test_codex_install_is_idempotent_and_preserves_existing_hooks(tmp_path: Path) -> None:
    project = tmp_path / "project with spaces"
    initialize_git(project)
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
    assert len(result.installed_events) == 11
    assert result.backup_path is not None
    assert Path(result.backup_path).exists()
    document = json.loads(config.read_text(encoding="utf-8"))
    pre_tool_groups = document["hooks"]["PreToolUse"]
    assert len(pre_tool_groups) == 2
    assert pre_tool_groups[0]["hooks"][0]["command"] == "echo existing"
    command = pre_tool_groups[1]["hooks"][0]["command"]
    assert "AGENT_DRIFT_GUARD=1" in command
    assert str(installer.executable) not in command
    assert "git rev-parse --show-toplevel" in command
    assert str(installer.executable) in installer.runner_path.read_text(encoding="utf-8")
    assert installer.runner_path.stat().st_mode & 0o777 == 0o700
    assert (project / ".agent-drift/anchors.json").exists()
    assert ".agent-drift/" in (project / ".gitignore").read_text(encoding="utf-8")
    assert result.gitignore_updated is True
    if os.name != "nt":
        assert installer.data_root.stat().st_mode & 0o777 == 0o700
        assert Path(result.backup_path).stat().st_mode & 0o777 == 0o600
        assert (project / ".agent-drift/anchors.json").stat().st_mode & 0o777 == 0o600
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
    config = project / ".claude/settings.local.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"permissions": {"allow": ["Read"]}}), encoding="utf-8")
    installer = HookInstaller("claude-code", project, executable=executable(tmp_path))
    result = installer.install()
    assert len(result.installed_events) == 14
    document = json.loads(config.read_text(encoding="utf-8"))
    assert document["permissions"] == {"allow": ["Read"]}
    assert "PostToolUseFailure" in document["hooks"]
    assert "PermissionRequest" in document["hooks"]
    assert "TaskCompleted" in document["hooks"]
    assert "StopFailure" in document["hooks"]
    assert ".claude/settings.local.json" in (project / ".gitignore").read_text(encoding="utf-8")


def test_install_dry_run_does_not_touch_project(tmp_path: Path) -> None:
    project = tmp_path / "project"
    initialize_git(project)
    installer = HookInstaller("codex", project, executable=executable(tmp_path))
    result = installer.install(dry_run=True)
    assert result.changed is True
    assert not installer.config_path.exists()
    assert not installer.data_root.exists()


@pytest.mark.skipif(os.name == "nt", reason="symlink behavior differs on Windows")
def test_installer_rejects_config_symlink_outside_project(tmp_path: Path) -> None:
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    initialize_git(project)
    outside.mkdir()
    (project / ".codex").symlink_to(outside, target_is_directory=True)
    with pytest.raises(HookInstallError, match="outside project root"):
        HookInstaller("codex", project, executable=executable(tmp_path))


def test_invalid_anchors_do_not_activate_hook_configuration(tmp_path: Path) -> None:
    project = tmp_path / "project"
    initialize_git(project)
    invalid = tmp_path / "invalid-anchors.json"
    invalid.write_text('{"task": {}}', encoding="utf-8")
    installer = HookInstaller("codex", project, executable=executable(tmp_path))
    with pytest.raises(HookInstallError, match="invalid anchors"):
        installer.install(anchors=invalid)
    assert not installer.config_path.exists()
    assert not (project / ".gitignore").exists()


def test_codex_install_rejects_a_non_git_project(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    installer = HookInstaller("codex", project, executable=executable(tmp_path))
    with pytest.raises(HookInstallError, match="Git worktree"):
        installer.install()


def test_codex_install_locates_a_nested_project_relative_to_git_root(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    initialize_git(repository)
    project = repository / "packages" / "app with spaces"
    project.mkdir(parents=True)
    installer = HookInstaller("codex", project, executable=executable(tmp_path))

    installer.install()

    document = json.loads(installer.config_path.read_text(encoding="utf-8"))
    command = document["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert "git rev-parse --show-toplevel" in command
    assert "packages/app with spaces/.agent-drift/codex-hook" in command
    assert installer.runner_path.exists()


def test_status_reports_a_missing_runner_as_unhealthy(tmp_path: Path) -> None:
    project = tmp_path / "project"
    initialize_git(project)
    installer = HookInstaller("codex", project, executable=executable(tmp_path))
    installer.install()
    installer.runner_path.unlink()

    result = installer.status()

    assert len(result.installed_events) == 11
    assert result.healthy is False
    assert "missing Hook runner" in result.health_issues


def test_status_rejects_tampered_and_duplicate_managed_handlers(tmp_path: Path) -> None:
    project = tmp_path / "project"
    initialize_git(project)
    installer = HookInstaller("codex", project, executable=executable(tmp_path))
    installer.install()
    document = json.loads(installer.config_path.read_text(encoding="utf-8"))
    handlers = document["hooks"]["PreToolUse"][0]["hooks"]
    handlers[0]["command"] = "AGENT_DRIFT_GUARD=1 true"
    handlers.append(dict(handlers[0]))
    installer.config_path.write_text(json.dumps(document), encoding="utf-8")

    result = installer.status()

    assert result.healthy is False
    assert "multiple Agent Drift Guard Hook handlers: PreToolUse" in result.health_issues


def test_status_rejects_a_tampered_managed_handler(tmp_path: Path) -> None:
    project = tmp_path / "project"
    initialize_git(project)
    installer = HookInstaller("codex", project, executable=executable(tmp_path))
    installer.install()
    document = json.loads(installer.config_path.read_text(encoding="utf-8"))
    handler = document["hooks"]["Stop"][0]["hooks"][0]
    handler["command"] = "AGENT_DRIFT_GUARD=1 true"
    installer.config_path.write_text(json.dumps(document), encoding="utf-8")

    result = installer.status()

    assert result.healthy is False
    assert "invalid Agent Drift Guard Hook handler: Stop" in result.health_issues


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes are not available on Windows")
def test_status_and_install_cover_all_runtime_permissions_and_gitignore(tmp_path: Path) -> None:
    project = tmp_path / "project"
    initialize_git(project)
    installer = HookInstaller("codex", project, executable=executable(tmp_path))
    installer.install()
    SQLiteStore(installer.data_root / "drift.db")
    telemetry = installer.data_root / "observations.jsonl"
    telemetry.write_text("{}\n", encoding="utf-8")
    nested = installer.data_root / "runtime" / "state.json"
    nested.parent.mkdir()
    nested.write_text("{}\n", encoding="utf-8")
    for path in (installer.config_path, installer.data_root / "drift.db", telemetry, nested):
        path.chmod(0o644)
    nested.parent.chmod(0o755)
    (project / ".gitignore").write_text("", encoding="utf-8")

    degraded = installer.status()

    assert degraded.healthy is False
    assert "private runtime permissions require repair" in degraded.health_issues
    assert "missing .gitignore entries: .agent-drift/" in degraded.health_issues

    repaired = installer.install()

    assert repaired.changed is True
    assert repaired.gitignore_updated is True
    assert installer.status().healthy is True
    assert installer.config_path.stat().st_mode & 0o777 == 0o600
    assert nested.parent.stat().st_mode & 0o777 == 0o700
    for path in (installer.data_root / "drift.db", telemetry, nested):
        assert path.stat().st_mode & 0o777 == 0o600


def test_install_changed_includes_anchor_and_gitignore_mutations(tmp_path: Path) -> None:
    project = tmp_path / "project"
    initialize_git(project)
    installer = HookInstaller("codex", project, executable=executable(tmp_path))
    installer.install()
    anchors = tmp_path / "anchors.json"
    anchors.write_text(
        json.dumps({"task": {"goal": "Updated production goal."}}),
        encoding="utf-8",
    )

    anchor_result = installer.install(anchors=anchors)
    assert anchor_result.changed is True

    (project / ".gitignore").unlink()
    gitignore_result = installer.install()
    assert gitignore_result.changed is True
    assert gitignore_result.gitignore_updated is True


def test_install_upgrades_only_the_legacy_generated_default_anchors(tmp_path: Path) -> None:
    project = tmp_path / "project"
    initialize_git(project)
    data_root = project / ".agent-drift"
    data_root.mkdir()
    anchors_path = data_root / "anchors.json"
    anchors_path.write_text(
        json.dumps(
            {
                "task": {
                    "goal": "Keep work aligned with the current user task and validate changes."
                },
                "repo": {
                    "validation_command_patterns": [
                        r"(?:^|\s)pytest(?:\s|$)",
                        r"(?:^|\s)(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test(?:\s|$)",
                        r"(?:^|\s)cargo\s+test(?:\s|$)",
                        r"(?:^|\s)go\s+test(?:\s|$)",
                        r"(?:^|\s)dotnet\s+test(?:\s|$)",
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    installer = HookInstaller("codex", project, executable=executable(tmp_path))

    result = installer.install()

    assert result.changed is True
    upgraded = json.loads(anchors_path.read_text(encoding="utf-8"))
    patterns = upgraded["repo"]["validation_command_patterns"]
    assert any("unittest" in pattern for pattern in patterns)
    assert all(r"(?:^|(?:&&|\|\||;)\s*)" in pattern for pattern in patterns)


def test_install_upgrades_the_v071_generated_default_anchors(tmp_path: Path) -> None:
    project = tmp_path / "project"
    initialize_git(project)
    data_root = project / ".agent-drift"
    data_root.mkdir()
    anchors_path = data_root / "anchors.json"
    anchors_path.write_text(
        json.dumps(
            {
                "task": {
                    "goal": "Keep work aligned with the current user task and validate changes."
                },
                "repo": {
                    "validation_command_patterns": [
                        r"(?:^|\s)pytest(?:\s|$)",
                        r"(?:^|(?:&&|\|\||;)\s*)(?:uv\s+run\s+)?(?:python(?:3(?:\.\d+)?)?\s+-m\s+)?unittest(?:\s|$)",
                        r"(?:^|\s)(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test(?:\s|$)",
                        r"(?:^|\s)cargo\s+test(?:\s|$)",
                        r"(?:^|\s)go\s+test(?:\s|$)",
                        r"(?:^|\s)dotnet\s+test(?:\s|$)",
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    installer = HookInstaller("codex", project, executable=executable(tmp_path))

    installer.install()

    upgraded = json.loads(anchors_path.read_text(encoding="utf-8"))
    patterns = upgraded["repo"]["validation_command_patterns"]
    assert all(r"(?:^|(?:&&|\|\||;)\s*)" in pattern for pattern in patterns)


def test_install_preserves_custom_constraints_and_plan_for_generated_default_repo(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    initialize_git(project)
    data_root = project / ".agent-drift"
    data_root.mkdir()
    anchors_path = data_root / "anchors.json"
    anchors_path.write_text(
        json.dumps(
            {
                "task": {
                    "goal": "Keep work aligned with the current user task and validate changes."
                },
                "constraints": {
                    "hard_constraints": ["Do not delete secrets."],
                    "soft_constraints": ["Run tests when possible."],
                },
                "plan": {
                    "milestones": [
                        {
                            "milestone_id": "m1",
                            "title": "Foundation",
                        }
                    ],
                    "current_milestone": "m1",
                },
                "repo": {
                    "validation_command_patterns": [
                        r"(?:^|\s)pytest(?:\s|$)",
                        r"(?:^|(?:&&|\|\||;)\s*)(?:uv\s+run\s+)?(?:python(?:3(?:\.\d+)?)?\s+-m\s+)?unittest(?:\s|$)",
                        r"(?:^|\s)(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test(?:\s|$)",
                        r"(?:^|\s)cargo\s+test(?:\s|$)",
                        r"(?:^|\s)go\s+test(?:\s|$)",
                        r"(?:^|\s)dotnet\s+test(?:\s|$)",
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    installer = HookInstaller("codex", project, executable=executable(tmp_path))

    installer.install()

    upgraded = json.loads((data_root / "anchors.json").read_text(encoding="utf-8"))
    assert upgraded["constraints"]["hard_constraints"] == ["Do not delete secrets."]
    assert upgraded["constraints"]["soft_constraints"] == ["Run tests when possible."]
    assert upgraded["plan"]["milestones"][0]["milestone_id"] == "m1"
    assert upgraded["plan"]["current_milestone"] == "m1"
    patterns = upgraded["repo"]["validation_command_patterns"]
    assert all(r"(?:^|(?:&&|\|\||;)\s*)" in pattern for pattern in patterns)
