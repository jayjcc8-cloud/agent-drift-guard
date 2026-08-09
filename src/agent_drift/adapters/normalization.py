"""Shared normalization helpers for hook-based coding agents."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any, cast

from pydantic import JsonValue, TypeAdapter, ValidationError

from agent_drift.adapters.base import AdapterError
from agent_drift.protocol.events import AgentEvent, EventType

_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])
_PATCH_PATH = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.MULTILINE)
_UNITTEST_FAILURE = re.compile(r"^\s*FAILED(?:\s*\([^\n]*\))?\s*$", re.MULTILINE)
_UNITTEST_SUCCESS = re.compile(r"^\s*OK(?:\s*\([^\n]*\))?\s*$", re.MULTILINE)

HOOK_EVENT_TYPES: dict[str, EventType] = {
    "SessionStart": EventType.SESSION_START,
    "UserPromptSubmit": EventType.PROMPT_SUBMIT,
    "PermissionRequest": EventType.PERMISSION_REQUEST,
    "PreToolUse": EventType.TOOL_BEFORE,
    "PostToolUse": EventType.TOOL_AFTER,
    "PostToolUseFailure": EventType.TOOL_ERROR,
    "PreCompact": EventType.COMPACTION_BEFORE,
    "PostCompact": EventType.COMPACTION_AFTER,
    "SubagentStart": EventType.SUBAGENT_START,
    "SubagentStop": EventType.SUBAGENT_STOP,
    "TaskCompleted": EventType.TASK_COMPLETED,
    "Stop": EventType.AGENT_STOP,
    "StopFailure": EventType.AGENT_ERROR,
    "SessionEnd": EventType.SESSION_END,
}


def json_object(raw: Mapping[str, Any]) -> dict[str, JsonValue]:
    try:
        return _JSON_OBJECT.validate_python(dict(raw))
    except ValidationError as exc:
        raise AdapterError(f"hook input must be a JSON object: {exc}") from exc


def required_string(raw: Mapping[str, JsonValue], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise AdapterError(f"hook input requires non-empty string field {key!r}")
    return value


def optional_string(raw: Mapping[str, JsonValue], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise AdapterError(f"hook field {key!r} must be a string or null")
    return value


def required_bool(raw: Mapping[str, JsonValue], key: str) -> bool:
    value = raw.get(key)
    if not isinstance(value, bool):
        raise AdapterError(f"hook input requires boolean field {key!r}")
    return value


def required_value(raw: Mapping[str, JsonValue], key: str) -> JsonValue:
    if key not in raw:
        raise AdapterError(f"hook input requires field {key!r}")
    return raw[key]


def canonical_tool_name(name: str) -> str:
    return {
        "Bash": "shell",
        "PowerShell": "shell",
        "apply_patch": "file.patch",
        "Edit": "file.edit",
        "Write": "file.write",
        "Read": "file.read",
        "Glob": "file.glob",
        "Grep": "file.search",
        "Agent": "subagent",
    }.get(name, name)


def _extract_paths(tool: str, arguments: JsonValue) -> list[str]:
    if not isinstance(arguments, dict):
        return []
    path_value = arguments.get("file_path") or arguments.get("path")
    paths = [path_value] if isinstance(path_value, str) else []
    if tool == "file.patch":
        command = arguments.get("command")
        if isinstance(command, str):
            paths.extend(_PATCH_PATH.findall(command))
    return sorted(set(paths))


def _infer_unittest_outcome(text: str) -> str | None:
    if _UNITTEST_FAILURE.search(text):
        return "failure"
    if _UNITTEST_SUCCESS.search(text):
        return "success"
    return None


def _infer_outcome(result: JsonValue, *, error_event: bool) -> str:
    if error_event:
        return "failure"
    if isinstance(result, str):
        # Codex 0.147.0 emits Bash PostToolUse responses as plain text. Real
        # controlled sessions showed unittest's terminal status line is the
        # only portable success/failure signal in that payload shape.
        return _infer_unittest_outcome(result) or "unknown"
    if not isinstance(result, dict):
        return "unknown"
    success = result.get("success")
    if isinstance(success, bool):
        return "success" if success else "failure"
    for key in ("exit_code", "exitCode", "status_code"):
        code = result.get(key)
        if isinstance(code, int) and not isinstance(code, bool):
            return "success" if code == 0 else "failure"
    is_error = result.get("is_error")
    if isinstance(is_error, bool):
        return "failure" if is_error else "success"
    # Claude Code 2.1.98 emits Bash PostToolUse responses as an object with
    # stdout/stderr but no exit code. unittest writes its terminal status to
    # either stream depending on the runner configuration.
    streams = "\n".join(
        value for key in ("stdout", "stderr") if isinstance((value := result.get(key)), str)
    )
    if streams:
        inferred = _infer_unittest_outcome(streams)
        if inferred is not None:
            return inferred
    return "unknown"


def normalized_payload(hook_name: str, raw: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    event_type = HOOK_EVENT_TYPES[hook_name]
    if event_type == EventType.SESSION_START:
        return {"source": required_string(raw, "source")}
    if event_type == EventType.SESSION_END:
        return {"reason": required_string(raw, "reason")}
    if event_type == EventType.PROMPT_SUBMIT:
        return {"prompt": required_string(raw, "prompt")}
    if event_type == EventType.PERMISSION_REQUEST:
        platform_tool = required_string(raw, "tool_name")
        tool = canonical_tool_name(platform_tool)
        arguments = required_value(raw, "tool_input")
        permission_payload: dict[str, JsonValue] = {
            "tool": tool,
            "arguments": arguments,
            "paths": cast(list[JsonValue], _extract_paths(tool, arguments)),
        }
        tool_call_id = optional_string(raw, "tool_use_id")
        if tool_call_id:
            permission_payload["tool_call_id"] = tool_call_id
        if "permission_suggestions" in raw:
            permission_payload["permission_suggestions"] = raw["permission_suggestions"]
        return permission_payload
    if event_type in {EventType.TOOL_BEFORE, EventType.TOOL_AFTER, EventType.TOOL_ERROR}:
        platform_tool = required_string(raw, "tool_name")
        tool = canonical_tool_name(platform_tool)
        arguments = required_value(raw, "tool_input")
        payload: dict[str, JsonValue] = {
            "tool": tool,
            "arguments": arguments,
            "tool_call_id": required_string(raw, "tool_use_id"),
            "paths": cast(list[JsonValue], _extract_paths(tool, arguments)),
        }
        if event_type in {EventType.TOOL_AFTER, EventType.TOOL_ERROR}:
            result_key = "error" if event_type == EventType.TOOL_ERROR else "tool_response"
            result = required_value(raw, result_key)
            payload["result"] = result
            payload["outcome"] = _infer_outcome(
                result, error_event=event_type == EventType.TOOL_ERROR
            )
        duration = raw.get("duration_ms")
        if duration is not None and (
            not isinstance(duration, int) or isinstance(duration, bool) or duration < 0
        ):
            raise AdapterError("hook field 'duration_ms' must be a non-negative integer")
        if isinstance(duration, int):
            payload["duration_ms"] = duration
        return payload
    if event_type in {EventType.COMPACTION_BEFORE, EventType.COMPACTION_AFTER}:
        payload = {"trigger": required_string(raw, "trigger")}
        if event_type == EventType.COMPACTION_BEFORE and "custom_instructions" in raw:
            payload["custom_instructions"] = optional_string(raw, "custom_instructions")
        if event_type == EventType.COMPACTION_AFTER and "compact_summary" in raw:
            payload["summary"] = optional_string(raw, "compact_summary")
        return payload
    if event_type in {EventType.SUBAGENT_START, EventType.SUBAGENT_STOP}:
        payload = {"agent_type": required_string(raw, "agent_type")}
        if event_type == EventType.SUBAGENT_STOP:
            payload.update(
                {
                    "stop_hook_active": required_bool(raw, "stop_hook_active"),
                    "last_message": optional_string(raw, "last_assistant_message"),
                }
            )
        return payload
    if event_type == EventType.TASK_COMPLETED:
        payload = {
            "task_id": required_string(raw, "task_id"),
            "task_subject": required_string(raw, "task_subject"),
        }
        for key in ("task_description", "teammate_name", "team_name"):
            if key in raw:
                payload[key] = optional_string(raw, key)
        return payload
    if event_type == EventType.AGENT_STOP:
        payload = {
            "stop_hook_active": required_bool(raw, "stop_hook_active"),
            "last_message": optional_string(raw, "last_assistant_message"),
        }
        for key in ("background_tasks", "session_crons"):
            if key in raw:
                payload[key] = raw[key]
        return payload
    if event_type == EventType.AGENT_ERROR:
        payload = {"error": required_string(raw, "error")}
        for key in ("error_details", "last_assistant_message"):
            if key in raw:
                payload[key] = optional_string(raw, key)
        return payload
    raise AdapterError(f"no payload profile for hook event {hook_name}")


def build_event(
    *,
    platform: str,
    platform_version: str | None,
    extension_namespace: str,
    allowed_hooks: frozenset[str],
    raw_input: Mapping[str, Any],
    timestamp: datetime | None,
    repo_root: str | None,
    sequence: int | None,
) -> AgentEvent:
    raw = json_object(raw_input)
    hook_name = required_string(raw, "hook_event_name")
    if hook_name not in HOOK_EVENT_TYPES or hook_name not in allowed_hooks:
        raise AdapterError(f"unsupported {platform} hook event {hook_name!r}")

    session_id = required_string(raw, "session_id")
    turn_id = optional_string(raw, "turn_id") or optional_string(raw, "prompt_id")
    agent_id = optional_string(raw, "agent_id") or "main"
    cwd = required_string(raw, "cwd")

    common_keys = {
        "session_id",
        "turn_id",
        "prompt_id",
        "transcript_path",
        "cwd",
        "permission_mode",
        "effort",
        "hook_event_name",
        "model",
        "agent_id",
        "agent_type",
    }
    payload_keys = {
        "source",
        "reason",
        "prompt",
        "permission_suggestions",
        "tool_name",
        "tool_input",
        "tool_use_id",
        "tool_response",
        "error",
        "duration_ms",
        "trigger",
        "custom_instructions",
        "compact_summary",
        "stop_hook_active",
        "last_assistant_message",
        "agent_transcript_path",
        "background_tasks",
        "session_crons",
        "task_id",
        "task_subject",
        "task_description",
        "teammate_name",
        "team_name",
        "error_details",
    }
    extra = {key: value for key, value in raw.items() if key not in common_keys | payload_keys}
    extensions: dict[str, JsonValue] = {
        f"{extension_namespace}.hook_event_name": hook_name,
    }
    for key in ("transcript_path", "permission_mode", "model", "prompt_id", "effort"):
        if key in raw:
            extensions[f"{extension_namespace}.{key}"] = raw[key]
    if (event_type := HOOK_EVENT_TYPES.get(hook_name)) and event_type in {
        EventType.TOOL_BEFORE,
        EventType.TOOL_AFTER,
        EventType.TOOL_ERROR,
        EventType.PERMISSION_REQUEST,
    }:
        extensions[f"{extension_namespace}.tool_name"] = raw["tool_name"]
    if extra:
        extensions[f"{extension_namespace}.extra"] = extra

    event_data: dict[str, Any] = {
        "event_type": HOOK_EVENT_TYPES[hook_name],
        "platform": platform,
        "platform_version": platform_version,
        "session_id": session_id,
        "turn_id": turn_id,
        "agent_id": agent_id,
        "repo_root": repo_root,
        "cwd": cwd,
        "sequence": sequence,
        "payload": normalized_payload(hook_name, raw),
        "extensions": extensions,
    }
    if timestamp is not None:
        event_data["timestamp"] = timestamp
    return AgentEvent.model_validate(event_data)
