"""Stateful, platform-neutral runtime supervisor."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable

from agent_drift.core.anchors import GuardAnchors
from agent_drift.core.evidence import SupervisionResult
from agent_drift.detectors import (
    ConstraintDetector,
    DetectionContext,
    Detector,
    LoopDetector,
    ScopeDetector,
    StateDetector,
    ValidationDetector,
)
from agent_drift.policies import DecisionPolicy
from agent_drift.protocol.events import AgentEvent
from agent_drift.store.base import EventStore


class Supervisor:
    """Evaluate normalized events and retain bounded per-session history."""

    def __init__(
        self,
        anchors: GuardAnchors,
        detectors: Iterable[Detector] | None = None,
        *,
        history_limit: int = 500,
        store: EventStore | None = None,
    ) -> None:
        if history_limit < 1:
            raise ValueError("history_limit must be positive")
        self._anchors = anchors
        self._detectors = tuple(
            detectors
            if detectors is not None
            else (
                ConstraintDetector(),
                ScopeDetector(),
                LoopDetector(),
                ValidationDetector(),
                StateDetector(),
            )
        )
        self._policy = DecisionPolicy(anchors)
        self._history_limit = history_limit
        self._store = store
        self._history: dict[str, deque[AgentEvent]] = defaultdict(
            lambda: deque(maxlen=self._history_limit)
        )
        self._sequences: dict[str, int] = defaultdict(int)

    @staticmethod
    def _detection_history(
        event: AgentEvent, history: tuple[AgentEvent, ...]
    ) -> tuple[AgentEvent, ...]:
        """Keep actor-local evidence inside the bounded session history."""

        return tuple(
            prior
            for prior in history
            if prior.platform == event.platform
            and prior.repo_root == event.repo_root
            and prior.agent_id == event.agent_id
        )

    def process(self, event: AgentEvent) -> SupervisionResult:
        event.assert_supported()
        if self._store is not None:
            existing = self._store.get_result(event.event_id)
            if existing is not None:
                return existing
            event = self._store.prepare_event(event)
            if event.sequence is None:
                raise RuntimeError("durable store returned an event without sequence")
            history = self._store.load_history(
                event.session_id,
                before_sequence=event.sequence,
                limit=self._history_limit,
            )
            history = self._detection_history(event, history)
            context = DetectionContext(event=event, history=history, anchors=self._anchors)
            evidence = tuple(
                item for detector in self._detectors for item in detector.detect(context)
            )
            result = SupervisionResult(
                event=event,
                evidence=evidence,
                decision=self._policy.decide(event, evidence),
            )
            return self._store.record_result(result)
        if event.sequence is None:
            event = event.model_copy(update={"sequence": self._sequences[event.session_id]})
        self._sequences[event.session_id] = max(
            self._sequences[event.session_id], (event.sequence or 0) + 1
        )
        history = tuple(self._history[event.session_id])
        history = self._detection_history(event, history)
        context = DetectionContext(event=event, history=history, anchors=self._anchors)
        evidence = tuple(item for detector in self._detectors for item in detector.detect(context))
        decision = self._policy.decide(event, evidence)
        self._history[event.session_id].append(event)
        return SupervisionResult(event=event, evidence=evidence, decision=decision)

    def history(self, session_id: str) -> tuple[AgentEvent, ...]:
        if self._store is not None:
            return self._store.load_history(
                session_id,
                limit=self._history_limit,
            )
        return tuple(self._history.get(session_id, ()))
