import unittest

from agent_drift.protocol.capabilities import (
    FULL_GUARD_REQUIREMENTS,
    Capability,
    PlatformCapabilities,
    ProtectionLevel,
)


class PlatformCapabilitiesTests(unittest.TestCase):
    def _model(self, capabilities: frozenset[Capability]) -> PlatformCapabilities:
        return PlatformCapabilities(
            platform="test-agent",
            adapter_version="0.1.0",
            capabilities=capabilities,
        )

    def test_no_capabilities_is_none(self) -> None:
        self.assertEqual(self._model(frozenset()).protection_level, ProtectionLevel.NONE)

    def test_observe_only_is_audit(self) -> None:
        model = self._model(frozenset({Capability.OBSERVE_TOOL}))
        self.assertEqual(model.protection_level, ProtectionLevel.AUDIT)

    def test_some_control_is_partial(self) -> None:
        model = self._model(frozenset({Capability.OBSERVE_TOOL, Capability.BLOCK_TOOL}))
        self.assertEqual(model.protection_level, ProtectionLevel.PARTIAL)

    def test_full_baseline_is_full(self) -> None:
        model = self._model(FULL_GUARD_REQUIREMENTS)
        assessment = model.assess()
        self.assertEqual(model.protection_level, ProtectionLevel.FULL)
        self.assertTrue(assessment.satisfied)
        self.assertEqual(assessment.coverage, 1.0)
        self.assertEqual(assessment.missing, frozenset())

    def test_custom_requirement(self) -> None:
        model = self._model(frozenset({Capability.OBSERVE_TOOL}))
        assessment = model.assess(frozenset({Capability.OBSERVE_TOOL, Capability.BLOCK_TOOL}))
        self.assertEqual(assessment.coverage, 0.5)
        self.assertEqual(assessment.missing, frozenset({Capability.BLOCK_TOOL}))

    def test_json_capabilities_are_stably_sorted(self) -> None:
        model = self._model(frozenset({Capability.OBSERVE_TOOL, Capability.BLOCK_TOOL}))
        document = model.model_dump(mode="json")
        self.assertEqual(
            document["capabilities"],
            ["control.block_tool", "observe.tool"],
        )


if __name__ == "__main__":
    unittest.main()
