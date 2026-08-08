"""Platform adapters for native agent lifecycle hooks."""

from agent_drift.adapters.base import (
    AdapterError,
    HookResponse,
    PlatformAdapter,
    UnsupportedDecisionError,
)
from agent_drift.adapters.claude import ClaudeCodeAdapter
from agent_drift.adapters.codex import CodexAdapter

__all__ = [
    "AdapterError",
    "ClaudeCodeAdapter",
    "CodexAdapter",
    "HookResponse",
    "PlatformAdapter",
    "UnsupportedDecisionError",
]
