"""Idempotent project-level Hook installation for Codex and Claude Code."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from agent_drift.core import GuardAnchors, TaskAnchor
from agent_drift.protocol.base import WireModel

PlatformName = Literal["codex", "claude-code"]
_MARKER = "AGENT_DRIFT_GUARD=1"
_EVENTS: dict[PlatformName, tuple[str, ...]] = {
    "codex": (
        "SessionStart",
        "UserPromptSubmit",
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
        "PreToolUse",
        "PostToolUse",
        "PostToolUseFailure",
        "PreCompact",
        "PostCompact",
        "SubagentStart",
        "SubagentStop",
        "Stop",
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

    @property
    def config_path(self) -> Path:
        relative = ".codex/hooks.json" if self.platform == "codex" else ".claude/settings.json"
        return self.project_root / relative

    @property
    def data_root(self) -> Path:
        return self.project_root / ".agent-drift"

    def _validate_paths(self) -> None:
        for path in (self.config_path.parent, self.data_root, self.project_root / ".gitignore"):
            resolved = path.resolve()
            if not resolved.is_relative_to(self.project_root):
                raise HookInstallError(f"refusing path outside project root: {path}")

    def _command(self) -> str:
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
        return f"{_MARKER} " + " ".join(shlex.quote(value) for value in values)

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
        backup_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup = backup_root / f"{self.platform}-{stamp}.json"
        shutil.copy2(self.config_path, backup)
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
                return GuardAnchors.model_validate_json(destination.read_text(encoding="utf-8"))
            return GuardAnchors(
                task=TaskAnchor(
                    goal="Keep work aligned with the current user task and validate changes."
                )
            )
        except (OSError, ValueError) as exc:
            raise HookInstallError(f"invalid anchors configuration: {exc}") from exc

    def _write_anchors(self, anchors: GuardAnchors) -> None:
        destination = self.data_root / "anchors.json"
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination.write_text(anchors.model_dump_json(indent=2), encoding="utf-8")
        os.chmod(destination, 0o600)

    def _ensure_gitignore(self) -> bool:
        path = self.project_root / ".gitignore"
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        lines = current.splitlines()
        if ".agent-drift/" in lines:
            return False
        separator = "" if not current or current.endswith("\n") else "\n"
        path.write_text(f"{current}{separator}.agent-drift/\n", encoding="utf-8")
        return True

    def install(
        self,
        *,
        anchors: str | Path | None = None,
        dry_run: bool = False,
    ) -> HookInstallResult:
        updated, installed = self._merged_config(install=True)
        current = self._read_config()
        changed = updated != current
        anchors_document = self._load_anchors(anchors)
        write_anchors = anchors is not None or not (self.data_root / "anchors.json").exists()
        backup: Path | None = None
        gitignore_updated = False
        if not dry_run:
            if changed:
                backup = self._backup()
            gitignore_updated = self._ensure_gitignore()
            if write_anchors:
                self._write_anchors(anchors_document)
            if changed:
                self._write_config(updated)
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
        if isinstance(hooks, dict):
            for event, groups in hooks.items():
                if not isinstance(groups, list):
                    continue
                for group in groups:
                    if not isinstance(group, dict):
                        continue
                    handlers = group.get("hooks")
                    if isinstance(handlers, list) and any(
                        _is_agent_drift_handler(handler) for handler in handlers
                    ):
                        installed.append(str(event))
                        break
        return HookInstallResult(
            platform=self.platform,
            action="status",
            config_path=str(self.config_path),
            changed=False,
            installed_events=tuple(sorted(installed)),
            review_required=False,
        )
