import unittest

from pydantic import ValidationError

from agent_drift.protocol.decisions import (
    DecisionAction,
    DriftType,
    GuardDecision,
    Severity,
)


class GuardDecisionTests(unittest.TestCase):
    def test_reanchor_decision(self) -> None:
        decision = GuardDecision(
            action=DecisionAction.REANCHOR,
            fallback_action=DecisionAction.WARN,
            severity=Severity.HIGH,
            drift_type=DriftType.STATE,
            score=0.63,
            reason="Acceptance tests still fail.",
            context="Return to milestone M3.",
        )
        self.assertEqual(decision.drift_type, DriftType.STATE)

    def test_retry_is_bounded(self) -> None:
        with self.assertRaises(ValidationError):
            GuardDecision(action=DecisionAction.RETRY, reason="Transient tool error.")

        decision = GuardDecision(
            action=DecisionAction.RETRY,
            reason="Transient tool error.",
            max_retries=2,
            retry_after_seconds=0.5,
        )
        self.assertEqual(decision.max_retries, 2)

    def test_allow_cannot_claim_drift(self) -> None:
        with self.assertRaises(ValidationError):
            GuardDecision(
                action=DecisionAction.ALLOW,
                drift_type=DriftType.GOAL,
                reason="Contradictory decision.",
            )

    def test_fallback_must_differ(self) -> None:
        with self.assertRaises(ValidationError):
            GuardDecision(
                action=DecisionAction.BLOCK,
                fallback_action=DecisionAction.BLOCK,
                reason="Duplicate action.",
            )


if __name__ == "__main__":
    unittest.main()
