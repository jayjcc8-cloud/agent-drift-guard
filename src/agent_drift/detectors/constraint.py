"""Detect exact tool and command constraint violations."""

from __future__ import annotations

import re

from agent_drift.core.evidence import DriftEvidence
from agent_drift.detectors.base import DetectionContext, Detector
from agent_drift.detectors.helpers import payload_string, tool_command
from agent_drift.protocol.decisions import DriftType, Severity
from agent_drift.protocol.events import EventType


class ConstraintDetector(Detector):
    name = "constraint"

    def detect(self, context: DetectionContext) -> tuple[DriftEvidence, ...]:
        event = context.event
        if event.event_type != EventType.TOOL_BEFORE:
            return ()
        tool = payload_string(event, "tool")
        constraints = context.anchors.constraints
        if tool is not None and tool in constraints.forbidden_tools:
            return (
                DriftEvidence(
                    detector=self.name,
                    drift_type=DriftType.CONSTRAINT,
                    severity=Severity.CRITICAL,
                    score=1.0,
                    confidence=1.0,
                    summary=f"Tool {tool!r} is explicitly forbidden.",
                    event_id=event.event_id,
                    facts={"tool": tool},
                ),
            )
        command = tool_command(event)
        if command is None:
            return ()
        matches = [
            pattern
            for pattern in constraints.forbidden_command_patterns
            if re.search(pattern, command)
        ]
        if not matches:
            return ()
        return (
            DriftEvidence(
                detector=self.name,
                drift_type=DriftType.CONSTRAINT,
                severity=Severity.CRITICAL,
                score=1.0,
                confidence=1.0,
                summary="Shell command matches an explicit forbidden pattern.",
                event_id=event.event_id,
                facts={"command": command, "matched_patterns": matches},
            ),
        )
