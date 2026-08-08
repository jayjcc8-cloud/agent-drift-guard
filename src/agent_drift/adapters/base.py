"""Adapter boundary shared by every native integration."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from pydantic import Field, JsonValue

from agent_drift.protocol.base import WireModel
from agent_drift.protocol.capabilities import PlatformCapabilities
from agent_drift.protocol.decisions import GuardDecision
from agent_drift.protocol.events import AgentEvent


class AdapterError(ValueError):
    """Raised when a native hook document violates its platform contract."""


class UnsupportedDecisionError(AdapterError):
    """Raised when neither a decision nor its explicit fallback can be enforced."""


class HookResponse(WireModel):
    """Native command-hook process response.

    A real hook writes `stdout` as JSON, writes `stderr`, then exits with `exit_code`.
    Keeping all three values together makes decision translation contract-testable.
    """

    stdout: dict[str, JsonValue] | None = None
    stderr: str = ""
    exit_code: int = Field(default=0, ge=0, le=255)
    applied_action: str


class PlatformAdapter(ABC):
    """Translate native hook input/output without leaking it into the core."""

    @property
    @abstractmethod
    def capabilities(self) -> PlatformCapabilities:
        raise NotImplementedError

    @abstractmethod
    def adapt_event(
        self,
        raw: Mapping[str, Any],
        *,
        timestamp: datetime | None = None,
        repo_root: str | None = None,
        sequence: int | None = None,
    ) -> AgentEvent:
        raise NotImplementedError

    @abstractmethod
    def render_decision(self, event: AgentEvent, decision: GuardDecision) -> HookResponse:
        raise NotImplementedError
