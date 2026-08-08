"""Detector interface and immutable evaluation context."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from agent_drift.core.anchors import GuardAnchors
from agent_drift.core.evidence import DriftEvidence
from agent_drift.protocol.events import AgentEvent


@dataclass(frozen=True)
class DetectionContext:
    event: AgentEvent
    history: tuple[AgentEvent, ...]
    anchors: GuardAnchors


class Detector(ABC):
    name: str

    @abstractmethod
    def detect(self, context: DetectionContext) -> tuple[DriftEvidence, ...]:
        raise NotImplementedError
