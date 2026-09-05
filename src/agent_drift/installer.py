"""Idempotent project-level Hook installation for Codex and Claude Code."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import stat
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from agent_drift.core import GuardAnchors, RepoAnchor, TaskAnchor
from agent_drift.protocol.base import WireModel

PlatformName = Literal["codex", "claude-code"]
HookMode = Literal["enforce", "observe"]
InstalledHookMode = Literal["enforce", "observe", "legacy", "unknown"]
_MARKER = "AGENT_DRIFT_GUARD=1"
_DEFAULT_TASK_GOAL = "Keep work aligned with the current user task and validate changes."
_LEGACY_DEFAULT_REPO = RepoAnchor(
    validation_command_patterns=(
        r"(?:^|\s)pytest(?:\s|$)",
        r"(?:^|\s)(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test(?:\s|$)",
        r"(?:^|\s)cargo\s+test(?:\s|$)",
        r"(?:^|\s)go\s+test(?:\s|$)",
        r"(?:^|\s)dotnet\s+test(?:\s|$)",
    )
)
_V071_DEFAULT_REPO = RepoAnchor(
    validation_command_patterns=(
        r"(?:^|\s)pytest(?:\s|$)",
        r"(?:^|(?:&&|\|\||;)\s*)(?:uv\s+run\s+)?(?:python(?:3(?:\.\d+)?)?\s+-m\s+)?unittest(?:\s|$)",
        r"(?:^|\s)(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test(?:\s|$)",
        r"(?:^|\s)cargo\s+test(?:\s|$)",
        r"(?:^|\s)go\s+test(?:\s|$)",
        r"(?:^|\s)dotnet\s+test(?:\s|$)",
    )
)
_EVENTS: dict[PlatformName, tuple[str, ...]] = {
    "codex": (
        "SessionStart",
        "UserPromptSubmit",
        "PermissionRequest",
        "PreToolUse",
        "PostToolUse",
        "PreCompact",
        "PostCompact",
        "SubagentStart",
        "SubagentStop",
        "Stop",
        "SessionEnd",
    ),
    "claude-code": (
        "SessionStart",
        "UserPromptSubmit",
        "PermissionRequest",
        "PreToolUse",
        "PostToolUse",
        "PostToolUseFailure",
        "PreCompact",
        "PostCompact",
        "SubagentStart",
        "SubagentStop",
        "TaskCompleted",
        "Stop",
        "StopFailure",
        "SessionEnd",
    ),
}


class HookInstallError(RuntimeError):
    pass


class HookInstallResult(WireModel):
    platform: PlatformName
    action: str
    config_path: str
    changed: bool
    installed_events: tuple[str, ...]
    backup_path: str | None = None
    review_required: bool = True
    gitignore_updated: bool = False
    healthy: bool | None = None
    health_issues: tuple[str, ...] = ()
    mode: InstalledHookMode | None = None


def _is_agent_drift_handler(handler: Any) -> bool:
    return isinstance(handler, dict) and _MARKER in str(handler.get("command", ""))


class HookInstaller:
    def __init__(
        self,
        platform: PlatformName,
        project_root: str | Path,
        *,
        executable: str | Path | None = None,
    ) -> None:
        self.platform = platform
        self.project_root = Path(project_root).expanduser().resolve()
        if not self.project_root.is_dir():
            raise HookInstallError(
                f"project root does not exist or is not a directory: {project_root}"
            )
        if executable is None:
            resolved = shutil.which("agent-drift")
            if resolved is None:
                raise HookInstallError(
                    "agent-drift executable is not on PATH; pass --executable explicitly"
                )
            self.executable = Path(resolved).resolve()
        else:
            self.executable = Path(executable).expanduser().resolve()
        if not self.executable.exists():
            raise HookInstallError(f"agent-drift executable does not exist: {self.executable}")
        if not os.access(self.executable, os.X_OK):
            raise HookInstallError(f"agent-drift executable is not executable: {self.executable}")
        self._validate_paths()
        self._codex_git_root: Path | None = None

    @property
    def config_path(self) -> Path:
        relative = (
            ".codex/hooks.json" if self.platform == "codex" else ".claude/settings.local.json"
        )
        return self.project_root / relative

    @property
    def data_root(self) -> Path:
        return self.project_root / ".agent-drift"

    @property
    def runner_path(self) -> Path:
        return self.data_root / f"{self.platform}-hook"

    def _validate_paths(self) -> None:
        for path in (
            self.config_path.parent,
            self.data_root,
            self.runner_path,
            self.project_root / ".gitignore",
        ):
            resolved = path.resolve()
            if not resolved.is_relative_to(self.project_root):
                raise HookInstallError(f"refusing path outside project root: {path}")

    def _command(self) -> str:
        if self.platform == "codex":
            git_root = self._resolve_codex_git_root()
            relative_runner = self.runner_path.relative_to(git_root).as_posix()
            runner = '"$(git rev-parse --show-toplevel)"/' + shlex.quote(relative_runner)
        else:
            runner = '"${CLAUDE_PROJECT_DIR}/.agent-drift/claude-code-hook"'
        return f"{_MARKER} {runner}"

    def _resolve_codex_git_root(self) -> Path:
        if self._codex_git_root is not None:
            return self._codex_git_root
        try:
            completed = subprocess.run(
                ["git", "-C", str(self.project_root), "rev-parse", "--show-toplevel"],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            raise HookInstallError(
                f"failed to locate Git for Codex Hook installation: {exc}"
            ) from exc
        if completed.returncode != 0 or not completed.stdout.strip():
            raise HookInstallError(
                "Codex project root must be inside a Git worktree because the shareable "
                "Hook command resolves its private runner from the Git root"
            )
        git_root = Path(completed.stdout.strip()).resolve()
        if not self.project_root.is_relative_to(git_root):
            raise HookInstallError(
                f"Codex project root is outside its reported Git worktree: {self.project_root}"
            )
        self._codex_git_root = git_root
        return git_root

    @staticmethod
    def _mode(path: Path) -> int:
        return stat.S_IMODE(path.stat().st_mode)

    @staticmethod
    def _ensure_private_directory(path: Path) -> None:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(path, 0o700)

    def _private_layout_needs_update(self) -> bool:
        if not self.data_root.is_dir():
            return True
        if os.name == "nt":
            return False
        if self._mode(self.data_root) != 0o700:
            return True
        if self.config_path.is_file() and self._mode(self.config_path) != 0o600:
            return True
        for path in self.data_root.rglob("*"):
            if path.is_symlink():
                continue
            if path.is_dir() and self._mode(path) != 0o700:
                return True
            if path.is_file():
                expected = 0o700 if path == self.runner_path else 0o600
                if self._mode(path) != expected:
                    return True
        return False

    def _secure_private_layout(self) -> None:
        self._ensure_private_directory(self.data_root)
        if os.name == "nt":
            return
        if self.config_path.is_file():
            os.chmod(self.config_path, 0o600)
        for path in self.data_root.rglob("*"):
            if path.is_symlink():
                continue
            if path.is_dir():
                os.chmod(path, 0o700)
            elif path.is_file():
                os.chmod(path, 0o700 if path == self.runner_path else 0o600)

    def _runner_document(self, mode: HookMode | Literal["legacy"]) -> str:
        values = [
            str(self.executable),
            "hook",
            self.platform,
            "-",
        ]
        if mode != "legacy":
            values.extend(("--mode", mode))
        values.extend(
            (
                "--database",
                str(self.data_root / "drift.db"),
                "--anchors",
                str(self.data_root / "anchors.json"),
                "--repo-root",
                str(self.project_root),
                "--telemetry-jsonl",
                str(self.data_root / "observations.jsonl"),
            )
        )
        marker = "" if mode == "legacy" else f"# AGENT_DRIFT_MODE={mode}\n"
        return (
            "#!/bin/sh\n"
            + marker
            + "exec "
            + " ".join(shlex.quote(value) for value in values)
            + "\n"
        )

    def _runner_mode(self) -> InstalledHookMode | None:
        if not self.runner_path.is_file():
            return None
        try:
            lines = self.runner_path.read_text(encoding="utf-8").splitlines()
            if not lines or lines[0] != "#!/bin/sh":
                return "unknown"
            marker: str | None = None
            if len(lines) == 2:
                command_line = lines[1]
            elif len(lines) == 3 and lines[1].startswith("# AGENT_DRIFT_MODE="):
                marker = lines[1].partition("=")[2]
                command_line = lines[2]
            else:
                return "unknown"
            if not command_line.startswith("exec "):
                return "unknown"
            values = shlex.split(command_line[len("exec ") :])
        except (OSError, ValueError):
            return "unknown"
        indexes = [index for index, value in enumerate(values) if value == "--mode"]
        if not indexes:
            return "legacy" if marker is None else "unknown"
        if len(indexes) != 1 or indexes[0] + 1 >= len(values):
            return "unknown"
        value = values[indexes[0] + 1]
        if value == "observe" and marker == "observe":
            return "observe"
        if value == "enforce" and marker == "enforce":
            return "enforce"
        return "unknown"

    def _runner_changed(self, mode: HookMode | Literal["legacy"]) -> bool:
        try:
            return not self.runner_path.exists() or self.runner_path.read_text(
                encoding="utf-8"
            ) != self._runner_document(mode)
        except OSError as exc:
            raise HookInstallError(f"failed to inspect Hook runner: {exc}") from exc

    def _write_runner(self, mode: HookMode | Literal["legacy"]) -> None:
        self._ensure_private_directory(self.runner_path.parent)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.runner_path.name}.", dir=self.runner_path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(self._runner_document(mode))
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o700)
            os.replace(temporary, self.runner_path)
        finally:
            temporary.unlink(missing_ok=True)

    def _handler(self, event: str) -> dict[str, Any]:
        handler: dict[str, Any] = {
            "type": "command",
            "command": self._command(),
            "timeout": 3 if event == "SessionEnd" else 10,
        }
        if self.platform == "codex":
            handler["statusMessage"] = "Agent Drift Guard supervision"
        return handler

    def _read_config(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return {}
        try:
            document = json.loads(self.config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise HookInstallError(f"invalid JSON in {self.config_path}: {exc}") from exc
        if not isinstance(document, dict):
            raise HookInstallError(f"Hook config must be a JSON object: {self.config_path}")
        return document

    @staticmethod
    def _without_agent_drift(groups: Any) -> list[dict[str, Any]]:
        if groups is None:
            return []
        if not isinstance(groups, list):
            raise HookInstallError("Hook event configuration must be an array")
        output: list[dict[str, Any]] = []
        for group in groups:
            if not isinstance(group, dict):
                raise HookInstallError("Hook matcher group must be an object")
            handlers = group.get("hooks")
            if not isinstance(handlers, list):
                raise HookInstallError("Hook matcher group requires a hooks array")
            remaining = [handler for handler in handlers if not _is_agent_drift_handler(handler)]
            if remaining:
                updated = dict(group)
                updated["hooks"] = remaining
                output.append(updated)
        return output

    @staticmethod
    def _has_agent_drift_handlers(document: dict[str, Any]) -> bool:
        hooks = document.get("hooks")
        if not isinstance(hooks, dict):
            return False
        return any(
            _is_agent_drift_handler(handler)
            for groups in hooks.values()
            if isinstance(groups, list)
            for group in groups
            if isinstance(group, dict)
            for handlers in (group.get("hooks"),)
            if isinstance(handlers, list)
            for handler in handlers
        )

    def _merged_config(self, *, install: bool) -> tuple[dict[str, Any], tuple[str, ...]]:
        document = self._read_config()
        hooks = document.get("hooks", {})
        if not isinstance(hooks, dict):
            raise HookInstallError("top-level hooks field must be an object")
        updated_hooks = dict(hooks)
        installed: list[str] = []
        for event in _EVENTS[self.platform]:
            groups = self._without_agent_drift(updated_hooks.get(event))
            if install:
                groups.append({"hooks": [self._handler(event)]})
                installed.append(event)
            if groups:
                updated_hooks[event] = groups
            else:
                updated_hooks.pop(event, None)
        updated = dict(document)
        if updated_hooks:
            updated["hooks"] = updated_hooks
        else:
            updated.pop("hooks", None)
        return updated, tuple(installed)

    def _backup(self) -> Path | None:
        if not self.config_path.exists():
            return None
        backup_root = self.data_root / "backups"
        self._ensure_private_directory(self.data_root)
        self._ensure_private_directory(backup_root)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup = backup_root / f"{self.platform}-{stamp}.json"
        shutil.copy2(self.config_path, backup)
        if os.name != "nt":
            os.chmod(backup, 0o600)
        return backup

    def _write_config(self, document: dict[str, Any]) -> None:
        self.config_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.config_path.name}.",
            dir=self.config_path.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(document, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            os.replace(temporary, self.config_path)
            os.chmod(self.config_path, 0o600)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _load_anchors(self, source: str | Path | None) -> GuardAnchors:
        destination = self.data_root / "anchors.json"
        try:
            if source is not None:
                return GuardAnchors.model_validate_json(
                    Path(source).expanduser().read_text(encoding="utf-8")
                )
            if destination.exists():
                current = GuardAnchors.model_validate_json(destination.read_text(encoding="utf-8"))
                if current.task.goal == _DEFAULT_TASK_GOAL and current.repo in (
                    _LEGACY_DEFAULT_REPO,
                    _V071_DEFAULT_REPO,
                ):
                    return GuardAnchors(
                        task=current.task,
                        constraints=current.constraints,
                        plan=current.plan,
                    )
                return current
            return GuardAnchors(task=TaskAnchor(goal=_DEFAULT_TASK_GOAL))
        except (OSError, ValueError) as exc:
            raise HookInstallError(f"invalid anchors configuration: {exc}") from exc

    def _write_anchors(self, anchors: GuardAnchors) -> None:
        destination = self.data_root / "anchors.json"
        self._ensure_private_directory(destination.parent)
        destination.write_text(anchors.model_dump_json(indent=2), encoding="utf-8")
        if os.name != "nt":
            os.chmod(destination, 0o600)

    def _anchors_changed(self, anchors: GuardAnchors) -> bool:
        destination = self.data_root / "anchors.json"
        if not destination.exists():
            return True
        try:
            current = GuardAnchors.model_validate_json(destination.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return True
        return current != anchors

    def _missing_gitignore_entries(self) -> tuple[str, ...]:
        path = self.project_root / ".gitignore"
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        lines = current.splitlines()
        required = [".agent-drift/"]
        if self.platform == "claude-code":
            required.append(".claude/settings.local.json")
        return tuple(entry for entry in required if entry not in lines)

    def _ensure_gitignore(self) -> bool:
        path = self.project_root / ".gitignore"
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        missing = self._missing_gitignore_entries()
        if not missing:
            return False
        separator = "" if not current or current.endswith("\n") else "\n"
        addition = "".join(f"{entry}\n" for entry in missing)
        path.write_text(f"{current}{separator}{addition}", encoding="utf-8")
        return True

    def install(
        self,
        *,
        anchors: str | Path | None = None,
        dry_run: bool = False,
        mode: HookMode | None = None,
    ) -> HookInstallResult:
        updated, installed = self._merged_config(install=True)
        current = self._read_config()
        current_mode = self._runner_mode()
        if mode is None:
            if current_mode == "unknown":
                raise HookInstallError(
                    "unknown Hook runtime mode; pass an explicit --mode to repair it"
                )
            if current_mode is None and self._has_agent_drift_handlers(current):
                raise HookInstallError(
                    "existing Hook runtime mode cannot be determined; pass an explicit --mode "
                    "to repair it"
                )
            effective_mode: HookMode | Literal["legacy"] = current_mode or "observe"
        else:
            effective_mode = mode
        config_changed = updated != current
        runner_changed = self._runner_changed(effective_mode)
        anchors_document = self._load_anchors(anchors)
        anchors_changed = self._anchors_changed(anchors_document)
        gitignore_changed = bool(self._missing_gitignore_entries())
        permissions_changed = self._private_layout_needs_update()
        changed = any(
            (
                config_changed,
                runner_changed,
                anchors_changed,
                gitignore_changed,
                permissions_changed,
            )
        )
        backup: Path | None = None
        gitignore_updated = False
        if not dry_run:
            self._secure_private_layout()
            if config_changed:
                backup = self._backup()
            gitignore_updated = self._ensure_gitignore()
            if anchors_changed:
                self._write_anchors(anchors_document)
            if runner_changed:
                self._write_runner(effective_mode)
            if config_changed:
                self._write_config(updated)
            self._secure_private_layout()
        return HookInstallResult(
            platform=self.platform,
            action="install-dry-run" if dry_run else "install",
            config_path=str(self.config_path),
            changed=changed,
            installed_events=installed,
            backup_path=str(backup) if backup else None,
            gitignore_updated=gitignore_updated,
            mode=effective_mode,
        )

    def uninstall(self, *, dry_run: bool = False) -> HookInstallResult:
        current_mode = self._runner_mode()
        updated, _ = self._merged_config(install=False)
        current = self._read_config()
        changed = updated != current
        backup: Path | None = None
        if changed and not dry_run:
            backup = self._backup()
            self._write_config(updated)
        return HookInstallResult(
            platform=self.platform,
            action="uninstall-dry-run" if dry_run else "uninstall",
            config_path=str(self.config_path),
            changed=changed,
            installed_events=(),
            backup_path=str(backup) if backup else None,
            review_required=False,
            mode=current_mode,
        )

    def status(self) -> HookInstallResult:
        document = self._read_config()
        hooks = document.get("hooks", {})
        installed: list[str] = []
        managed_handlers: dict[str, list[dict[str, Any]]] = {}
        if isinstance(hooks, dict):
            for event, groups in hooks.items():
                if not isinstance(groups, list):
                    continue
                event_handlers: list[dict[str, Any]] = []
                for group in groups:
                    if not isinstance(group, dict):
                        continue
                    handlers = group.get("hooks")
                    if isinstance(handlers, list):
                        event_handlers.extend(
                            handler for handler in handlers if _is_agent_drift_handler(handler)
                        )
                if event_handlers:
                    event_name = str(event)
                    installed.append(event_name)
                    managed_handlers[event_name] = event_handlers
        issues: list[str] = []
        mode = self._runner_mode()
        missing_events = sorted(set(_EVENTS[self.platform]) - set(installed))
        if missing_events:
            issues.append("missing Hook handlers: " + ", ".join(missing_events))
        for event in _EVENTS[self.platform]:
            handlers = managed_handlers.get(event, [])
            if len(handlers) > 1:
                issues.append(f"multiple Agent Drift Guard Hook handlers: {event}")
            elif handlers and handlers[0] != self._handler(event):
                issues.append(f"invalid Agent Drift Guard Hook handler: {event}")
        unexpected_events = sorted(set(managed_handlers) - set(_EVENTS[self.platform]))
        if unexpected_events:
            issues.append(
                "unexpected Agent Drift Guard Hook handlers: " + ", ".join(unexpected_events)
            )
        if not self.runner_path.exists():
            issues.append("missing Hook runner")
        elif not self.runner_path.is_file():
            issues.append("Hook runner is not a regular file")
        elif not os.access(self.runner_path, os.X_OK):
            issues.append("Hook runner is not executable")
        elif mode == "unknown":
            issues.append("unknown Hook runtime mode")
        elif mode is not None and self._runner_changed(mode):
            issues.append("Hook runner does not match the current installation")
        anchors_path = self.data_root / "anchors.json"
        if not anchors_path.is_file():
            issues.append("missing anchors configuration")
        else:
            try:
                GuardAnchors.model_validate_json(anchors_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                issues.append("invalid anchors configuration")
        if self._private_layout_needs_update():
            issues.append("private runtime permissions require repair")
        missing_gitignore = self._missing_gitignore_entries()
        if missing_gitignore:
            issues.append("missing .gitignore entries: " + ", ".join(missing_gitignore))
        return HookInstallResult(
            platform=self.platform,
            action="status",
            config_path=str(self.config_path),
            changed=False,
            installed_events=tuple(sorted(installed)),
            review_required=bool(issues),
            healthy=not issues,
            health_issues=tuple(issues),
            mode=mode,
        )
