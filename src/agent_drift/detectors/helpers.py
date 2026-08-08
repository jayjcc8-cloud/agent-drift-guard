"""Pure helpers shared by deterministic detectors."""

from __future__ import annotations

import re
from collections.abc import Iterable

from pydantic import JsonValue

from agent_drift.protocol.events import AgentEvent, EventType

WRITE_TOOLS = frozenset({"file.patch", "file.edit", "file.write"})


def payload_string(event: AgentEvent, key: str) -> str | None:
    value = event.payload.get(key)
    return value if isinstance(value, str) else None


def payload_strings(event: AgentEvent, key: str) -> tuple[str, ...]:
    value = event.payload.get(key)
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def tool_arguments(event: AgentEvent) -> dict[str, JsonValue]:
    value = event.payload.get("arguments")
    return value if isinstance(value, dict) else {}


def tool_command(event: AgentEvent) -> str | None:
    value = tool_arguments(event).get("command")
    return value if isinstance(value, str) else None


def is_write_event(event: AgentEvent) -> bool:
    return (
        event.event_type == EventType.TOOL_BEFORE and payload_string(event, "tool") in WRITE_TOOLS
    )


def is_validation_event(event: AgentEvent, patterns: Iterable[str]) -> bool:
    if event.event_type not in {EventType.TOOL_AFTER, EventType.TOOL_ERROR}:
        return False
    if payload_string(event, "tool") != "shell":
        return False
    command = tool_command(event)
    return command is not None and any(re.search(pattern, command) for pattern in patterns)


def latest_write_index(history: tuple[AgentEvent, ...]) -> int | None:
    for index in range(len(history) - 1, -1, -1):
        if is_write_event(history[index]):
            return index
    return None


def validations_after(
    history: tuple[AgentEvent, ...], index: int, patterns: Iterable[str]
) -> tuple[AgentEvent, ...]:
    return tuple(event for event in history[index + 1 :] if is_validation_event(event, patterns))
