"""Detect deterministic completion claims contradicted by failed validation."""

from __future__ import annotations

import re

from agent_drift.core.evidence import DriftEvidence
from agent_drift.detectors.base import DetectionContext, Detector
from agent_drift.detectors.helpers import latest_write_index, payload_string, validations_after
from agent_drift.protocol.decisions import DriftType, Severity
from agent_drift.protocol.events import EventType

_COMPLETION_CLAIM = re.compile(
    r"\b(?:complete|completed|done|finished|all tests pass(?:ed)?)\b|(?:已完成|全部完成|测试通过)",
    re.IGNORECASE,
)


class StateDetector(Detector):
    name = "state"

    def detect(self, context: DetectionContext) -> tuple[DriftEvidence, ...]:
        event = context.event
        if event.event_type not in {EventType.AGENT_STOP, EventType.SUBAGENT_STOP}:
            return ()
        message = payload_string(event, "last_message")
        if message is None or _COMPLETION_CLAIM.search(message) is None:
            return ()
        last_write = latest_write_index(context.history)
        if last_write is None:
            return ()
        validations = validations_after(
            context.history,
            last_write,
            context.anchors.repo.validation_command_patterns,
        )
        if not validations or payload_string(validations[-1], "outcome") != "failure":
            return ()
        return (
            DriftEvidence(
                detector=self.name,
                drift_type=DriftType.STATE,
                severity=Severity.HIGH,
                score=0.98,
                confidence=1.0,
                summary="Completion claim contradicts the latest failed validation result.",
                event_id=event.event_id,
                facts={"last_message": message, "latest_validation_outcome": "failure"},
            ),
        )
