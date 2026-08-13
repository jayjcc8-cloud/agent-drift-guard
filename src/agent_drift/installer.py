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

    def _runner_document(self) -> str:
        values = (
            str(self.executable),
            "hook",
            self.platform,
            "-",
            "--database",
            str(self.data_root / "drift.db"),
            "--anchors",
            str(self.data_root / "anchors.json"),
            "--repo-root",
            str(self.project_root),
            "--telemetry-jsonl",
            str(self.data_root / "observations.jsonl"),
        )
        return "#!/bin/sh\nexec " + " ".join(shlex.quote(value) for value in values) + "\n"

    def _runner_changed(self) -> bool:
        try:
            return (
                not self.runner_path.exists()
                or self.runner_path.read_text(encoding="utf-8") != self._runner_document()
            )
        except OSError as exc:
            raise HookInstallError(f"failed to inspect Hook runner: {exc}") from exc

    def _write_runner(self) -> None:
        self._ensure_private_directory(self.runner_path.parent)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.runner_path.name}.", dir=self.runner_path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(self._runner_document())
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
                generated_defaults = (
                    GuardAnchors(
                        task=TaskAnchor(goal=_DEFAULT_TASK_GOAL),
                        repo=_LEGACY_DEFAULT_REPO,
                    ),
                    GuardAnchors(
                        task=TaskAnchor(goal=_DEFAULT_TASK_GOAL),
                        repo=_V071_DEFAULT_REPO,
                    ),
                )
                if current in generated_defaults:
                    return GuardAnchors(task=TaskAnchor(goal=_DEFAULT_TASK_GOAL))
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
    ) -> HookInstallResult:
        updated, installed = self._merged_config(install=True)
        current = self._read_config()
        config_changed = updated != current
        runner_changed = self._runner_changed()
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
                self._write_runner()
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
        )

    def uninstall(self, *, dry_run: bool = False) -> HookInstallResult:
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
        elif self._runner_changed():
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
        )
