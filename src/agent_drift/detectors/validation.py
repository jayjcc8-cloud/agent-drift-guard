"""Detect stopping after writes without successful validation."""

from __future__ import annotations

from pydantic import JsonValue

from agent_drift.core.evidence import DriftEvidence
from agent_drift.detectors.base import DetectionContext, Detector
from agent_drift.detectors.helpers import latest_write_index, payload_string, validations_after
from agent_drift.protocol.decisions import DriftType, Severity
from agent_drift.protocol.events import EventType


class ValidationDetector(Detector):
    name = "validation"

    def detect(self, context: DetectionContext) -> tuple[DriftEvidence, ...]:
        event = context.event
        if event.event_type not in {EventType.AGENT_STOP, EventType.SUBAGENT_STOP}:
            return ()
        last_write = latest_write_index(context.history)
        if last_write is None:
            return ()
        validations = validations_after(
            context.history,
            last_write,
            context.anchors.repo.validation_command_patterns,
        )
        if validations and payload_string(validations[-1], "outcome") == "success":
            return ()
        if validations and payload_string(validations[-1], "outcome") == "failure":
            summary = "Agent is stopping after the latest validation command failed."
            severity = Severity.HIGH
            score = 0.95
            facts: dict[str, JsonValue] = {"latest_validation_outcome": "failure"}
        else:
            summary = "Agent is stopping after repository writes without successful validation."
            severity = Severity.MEDIUM
            score = 0.75
            facts = {"validation_events_after_write": len(validations)}
        return (
            DriftEvidence(
                detector=self.name,
                drift_type=DriftType.VALIDATION,
                severity=severity,
                score=score,
                confidence=1.0,
                summary=summary,
                event_id=event.event_id,
                facts=facts,
            ),
        )
