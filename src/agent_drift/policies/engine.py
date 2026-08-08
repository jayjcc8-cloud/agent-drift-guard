"""Deterministic mapping from evidence to platform-neutral decisions."""

from __future__ import annotations

from agent_drift.core.anchors import GuardAnchors
from agent_drift.core.evidence import DriftEvidence
from agent_drift.protocol.decisions import DecisionAction, GuardDecision, Severity
from agent_drift.protocol.events import AgentEvent, EventType

_SEVERITY_ORDER = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


class DecisionPolicy:
    def __init__(self, anchors: GuardAnchors) -> None:
        self._anchors = anchors

    def decide(self, event: AgentEvent, evidence: tuple[DriftEvidence, ...]) -> GuardDecision:
        if not evidence:
            return GuardDecision(
                source_event_id=event.event_id,
                action=DecisionAction.ALLOW,
                severity=Severity.INFO,
                reason="No deterministic drift detected.",
            )
        primary = max(evidence, key=lambda item: (_SEVERITY_ORDER[item.severity], item.score))
        if primary.severity == Severity.LOW:
            action = DecisionAction.WARN
        elif event.event_type == EventType.TOOL_BEFORE and primary.severity in {
            Severity.HIGH,
            Severity.CRITICAL,
        }:
            action = DecisionAction.BLOCK
        elif event.event_type in {EventType.AGENT_STOP, EventType.SUBAGENT_STOP}:
            action = DecisionAction.CONTINUE
        else:
            action = DecisionAction.REANCHOR
        fallback = (
            DecisionAction.WARN
            if action in {DecisionAction.BLOCK, DecisionAction.CONTINUE, DecisionAction.REANCHOR}
            else None
        )
        summaries = " ".join(item.summary for item in evidence)
        criteria = "\n".join(f"- {item}" for item in self._anchors.task.acceptance_criteria)
        context = f"Re-anchor to the task goal: {self._anchors.task.goal}"
        if criteria:
            context += f"\nAcceptance criteria:\n{criteria}"
        context += f"\nDetected evidence: {summaries}"
        return GuardDecision(
            source_event_id=event.event_id,
            action=action,
            fallback_action=fallback,
            severity=primary.severity,
            drift_type=primary.drift_type,
            score=max(item.score for item in evidence),
            confidence=min(item.confidence for item in evidence),
            reason=summaries,
            context=context,
            evidence_ids=tuple(item.evidence_id for item in evidence),
        )
