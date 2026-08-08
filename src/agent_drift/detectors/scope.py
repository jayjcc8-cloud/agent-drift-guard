"""Detect writes outside the explicitly allowed repository scope."""

from __future__ import annotations

import fnmatch

from agent_drift.core.evidence import DriftEvidence
from agent_drift.detectors.base import DetectionContext, Detector
from agent_drift.detectors.helpers import WRITE_TOOLS, payload_string, payload_strings
from agent_drift.protocol.decisions import DriftType, Severity
from agent_drift.protocol.events import EventType


def _relative_path(path: str, repo_root: str | None) -> tuple[str, bool]:
    normalized = path.replace("\\", "/").rstrip("/")
    if repo_root is None:
        return normalized.lstrip("/"), True
    root = repo_root.replace("\\", "/").rstrip("/")
    if normalized == root:
        return ".", True
    prefix = root + "/"
    if normalized.startswith(prefix):
        return normalized[len(prefix) :], True
    if normalized.startswith("/") or (len(normalized) > 2 and normalized[1:3] == ":/"):
        return normalized, False
    return normalized.lstrip("./"), True


def _matches(path: str, pattern: str) -> bool:
    normalized = pattern.replace("\\", "/").lstrip("./")
    if normalized.endswith("/**"):
        root = normalized[:-3].rstrip("/")
        return path == root or path.startswith(root + "/")
    return fnmatch.fnmatchcase(path, normalized)


class ScopeDetector(Detector):
    name = "scope"

    def detect(self, context: DetectionContext) -> tuple[DriftEvidence, ...]:
        event = context.event
        allowed = context.anchors.constraints.allowed_write_paths
        if (
            event.event_type != EventType.TOOL_BEFORE
            or payload_string(event, "tool") not in WRITE_TOOLS
            or not allowed
        ):
            return ()
        violations: list[str] = []
        for raw_path in payload_strings(event, "paths"):
            relative, inside_repo = _relative_path(raw_path, event.repo_root)
            if not inside_repo or not any(_matches(relative, pattern) for pattern in allowed):
                violations.append(raw_path)
        if not violations:
            return ()
        return (
            DriftEvidence(
                detector=self.name,
                drift_type=DriftType.SCOPE,
                severity=Severity.HIGH,
                score=0.95,
                confidence=1.0,
                summary="Write target is outside the allowed task scope.",
                event_id=event.event_id,
                facts={"violating_paths": violations, "allowed_patterns": list(allowed)},
            ),
        )
