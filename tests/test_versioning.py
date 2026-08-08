import unittest

from agent_drift.protocol.versioning import ProtocolVersion


class ProtocolVersionTests(unittest.TestCase):
    def test_pre_one_minor_versions_are_breaking(self) -> None:
        self.assertFalse(ProtocolVersion("0.2").is_compatible_with("0.1"))
        self.assertTrue(ProtocolVersion("0.1").is_compatible_with("0.1"))

    def test_stable_minor_versions_are_backward_compatible(self) -> None:
        self.assertTrue(ProtocolVersion("1.2").is_compatible_with("1.4"))
        self.assertFalse(ProtocolVersion("1.5").is_compatible_with("1.4"))
        self.assertFalse(ProtocolVersion("2.0").is_compatible_with("1.9"))

    def test_invalid_version_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ProtocolVersion("v1")


if __name__ == "__main__":
    unittest.main()
