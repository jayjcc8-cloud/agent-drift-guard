"""Detect repeated identical tool calls and simple two-state oscillation."""

from __future__ import annotations

import json

from agent_drift.core.evidence import DriftEvidence
from agent_drift.detectors.base import DetectionContext, Detector
from agent_drift.protocol.decisions import DriftType, Severity
from agent_drift.protocol.events import AgentEvent, EventType


def _fingerprint(event: AgentEvent) -> str:
    data = {"tool": event.payload.get("tool"), "arguments": event.payload.get("arguments")}
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class LoopDetector(Detector):
    name = "loop"

    def __init__(
        self,
        repeat_threshold: int = 5,
        failure_threshold: int = 5,
        oscillation_cycles: int = 3,
    ) -> None:
        if repeat_threshold < 2 or failure_threshold < 2 or oscillation_cycles < 2:
            raise ValueError("loop thresholds must be at least 2")
        self._repeat_threshold = repeat_threshold
        self._failure_threshold = failure_threshold
        self._oscillation_cycles = oscillation_cycles

    def detect(self, context: DetectionContext) -> tuple[DriftEvidence, ...]:
        event = context.event
        if event.event_type in {EventType.TOOL_AFTER, EventType.TOOL_ERROR}:
            if event.payload.get("outcome") != "failure":
                return ()
            results = [
                item
                for item in context.history
                if item.event_type in {EventType.TOOL_AFTER, EventType.TOOL_ERROR}
            ]
            failures = 0
            current = _fingerprint(event)
            for item in reversed([*results, event]):
                if item.payload.get("outcome") != "failure" or _fingerprint(item) != current:
                    break
                failures += 1
            if failures >= self._failure_threshold:
                return (
                    DriftEvidence(
                        detector=self.name,
                        drift_type=DriftType.LOOP,
                        severity=Severity.HIGH,
                        score=min(1.0, 0.75 + failures * 0.04),
                        confidence=1.0,
                        summary=f"Identical tool call failed {failures} consecutive times.",
                        event_id=event.event_id,
                        facts={"failure_count": failures, "fingerprint": current},
                    ),
                )
            return ()
        if event.event_type != EventType.TOOL_BEFORE:
            return ()
        calls = [item for item in context.history if item.event_type == EventType.TOOL_BEFORE]
        fingerprints = [_fingerprint(item) for item in calls] + [_fingerprint(event)]
        current = fingerprints[-1]
        repeat_count = 0
        for fingerprint in reversed(fingerprints):
            if fingerprint != current:
                break
            repeat_count += 1
        if repeat_count >= self._repeat_threshold:
            return (
                DriftEvidence(
                    detector=self.name,
                    drift_type=DriftType.LOOP,
                    severity=Severity.HIGH,
                    score=min(1.0, 0.7 + repeat_count * 0.05),
                    confidence=1.0,
                    summary=f"Identical tool call repeated {repeat_count} consecutive times.",
                    event_id=event.event_id,
                    facts={"repeat_count": repeat_count, "fingerprint": current},
                ),
            )
        window_size = self._oscillation_cycles * 2
        if len(fingerprints) >= window_size:
            window = fingerprints[-window_size:]
            if window[0] != window[1] and all(
                item == window[index % 2] for index, item in enumerate(window)
            ):
                return (
                    DriftEvidence(
                        detector=self.name,
                        drift_type=DriftType.LOOP,
                        severity=Severity.HIGH,
                        score=0.9,
                        confidence=1.0,
                        summary=(
                            f"Tool calls are oscillating across {self._oscillation_cycles} cycles."
                        ),
                        event_id=event.event_id,
                        facts={"cycles": self._oscillation_cycles},
                    ),
                )
        return ()
