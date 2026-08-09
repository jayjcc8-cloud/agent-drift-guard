import unittest
from datetime import datetime

from pydantic import ValidationError

from agent_drift.protocol.events import AgentEvent, EventType


class AgentEventTests(unittest.TestCase):
    def _event(self, **overrides: object) -> AgentEvent:
        data: dict[str, object] = {
            "event_type": EventType.TOOL_BEFORE,
            "platform": "test-agent",
            "session_id": "s1",
            "timestamp": "2026-08-08T08:00:00Z",
            "payload": {"tool": "shell", "arguments": {"command": "pytest"}},
        }
        data.update(overrides)
        return AgentEvent.model_validate(data)

    def test_round_trip_is_json_safe(self) -> None:
        event = self._event(extensions={"test.raw": {"hook": "before"}})
        restored = AgentEvent.model_validate_json(event.model_dump_json())
        self.assertEqual(restored, event)
        self.assertIsInstance(restored.timestamp, datetime)

    def test_naive_timestamp_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self._event(timestamp="2026-08-08T08:00:00")

    def test_extension_keys_must_be_namespaced(self) -> None:
        with self.assertRaises(ValidationError):
            self._event(extensions={"raw": True})

    def test_unknown_core_field_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self._event(platfrom="typo")

    def test_json_payload_rejects_runtime_objects(self) -> None:
        with self.assertRaises(ValidationError):
            self._event(payload={"bad": object()})

    def test_incompatible_protocol_is_rejected_by_consumer(self) -> None:
        event = self._event(protocol_version="0.2")
        with self.assertRaises(ValueError):
            event.assert_supported("0.1")

    def test_v01_event_remains_readable(self) -> None:
        event = self._event(protocol_version="0.1")
        event.assert_supported()

    def test_new_event_types_default_to_v02_but_pre_release_v01_remains_readable(self) -> None:
        self.assertEqual(
            self._event(event_type=EventType.PERMISSION_REQUEST).protocol_version,
            "0.2",
        )
        legacy = self._event(
            event_type=EventType.PERMISSION_REQUEST,
            protocol_version="0.1",
        )
        legacy.assert_supported()


if __name__ == "__main__":
    unittest.main()
