"""Deterministic replay of sanitized long-session observations."""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import Field

from agent_drift.core import GuardAnchors, Supervisor
from agent_drift.observability import ObservationEnvelope
from agent_drift.protocol.base import WireModel
from agent_drift.protocol.decisions import DecisionAction, DriftType
from agent_drift.protocol.events import AgentEvent, EventType
from agent_drift.store.base import EventStore


class ReplayCase(WireModel):
    event: AgentEvent
    expected_action: DecisionAction | None = None


class ReplayEntry(WireModel):
    index: int = Field(ge=0)
    event_id: str
    session_id: str
    event_type: EventType
    expected_action: DecisionAction | None = None
    actual_action: DecisionAction
    matches_expected: bool | None = None
    drift_types: tuple[DriftType, ...] = ()
    detectors: tuple[str, ...] = ()


class ReplayReport(WireModel):
    schema_version: str = "0.1"
    source: str
    total_events: int
    sessions: int
    decision_counts: dict[str, int]
    evidence_counts: dict[str, int]
    compared_events: int
    mismatches: int
    semantic_fingerprint: str
    entries: tuple[ReplayEntry, ...]


class ReplayExportResult(WireModel):
    session_id: str
    output_path: str
    events: int
    expected_decisions: int


def load_replay_cases(path: str | Path) -> tuple[ReplayCase, ...]:
    source = Path(path)
    cases: list[ReplayCase] = []
    for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            document: Any = json.loads(line)
            if isinstance(document, dict) and "supervision" in document:
                observation = ObservationEnvelope.model_validate(document)
                cases.append(
                    ReplayCase(
                        event=observation.supervision.event,
                        expected_action=observation.supervision.decision.action,
                    )
                )
            elif isinstance(document, dict) and "event" in document:
                event = AgentEvent.model_validate(document["event"])
                expected = document.get("expected_action")
                cases.append(ReplayCase(event=event, expected_action=expected))
            else:
                cases.append(ReplayCase(event=AgentEvent.model_validate(document)))
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"invalid replay record at line {line_number}: {exc}") from exc
    if not cases:
        raise ValueError("replay input contains no events")
    return tuple(cases)


def write_replay_cases(cases: Iterable[ReplayCase], path: str | Path) -> int:
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = "".join(case.model_dump_json(exclude_none=True) + "\n" for case in cases)
    descriptor = os.open(
        destination,
        os.O_CREAT | os.O_TRUNC | os.O_WRONLY,
        0o600,
    )
    try:
        encoded = payload.encode("utf-8")
        written = os.write(descriptor, encoded)
        if written != len(encoded):
            raise OSError(f"short replay write: {written} of {len(encoded)} bytes")
    finally:
        os.close(descriptor)
    return payload.count("\n")


def export_store_session(
    store: EventStore,
    session_id: str,
    path: str | Path,
    *,
    limit: int = 5000,
) -> ReplayExportResult:
    events = store.load_history(session_id, limit=limit)
    cases: list[ReplayCase] = []
    expected = 0
    for event in events:
        result = store.get_result(event.event_id)
        action = result.decision.action if result is not None else None
        if action is not None:
            expected += 1
        cases.append(ReplayCase(event=event, expected_action=action))
    output_path = str(Path(path).expanduser().resolve())
    count = write_replay_cases(cases, output_path)
    return ReplayExportResult(
        session_id=session_id,
        output_path=output_path,
        events=count,
        expected_decisions=expected,
    )


def run_replay(
    cases: tuple[ReplayCase, ...],
    anchors: GuardAnchors,
    *,
    source: str = "memory",
) -> ReplayReport:
    supervisor = Supervisor(anchors)
    entries: list[ReplayEntry] = []
    decision_counts: Counter[str] = Counter()
    evidence_counts: Counter[str] = Counter()
    semantic_records: list[dict[str, Any]] = []
    sessions: set[str] = set()
    compared = 0
    mismatches = 0
    for index, case in enumerate(cases):
        result = supervisor.process(case.event)
        action = result.decision.action
        decision_counts[action.value] += 1
        sessions.add(result.event.session_id)
        drift_types = tuple(item.drift_type for item in result.evidence)
        detectors = tuple(item.detector for item in result.evidence)
        for drift_type in drift_types:
            evidence_counts[drift_type.value] += 1
        matches: bool | None = None
        if case.expected_action is not None:
            compared += 1
            matches = action == case.expected_action
            if not matches:
                mismatches += 1
        entries.append(
            ReplayEntry(
                index=index,
                event_id=str(result.event.event_id),
                session_id=result.event.session_id,
                event_type=result.event.event_type,
                expected_action=case.expected_action,
                actual_action=action,
                matches_expected=matches,
                drift_types=drift_types,
                detectors=detectors,
            )
        )
        semantic_records.append(
            {
                "index": index,
                "event_type": result.event.event_type.value,
                "action": action.value,
                "evidence": [
                    {
                        "detector": item.detector,
                        "drift_type": item.drift_type.value,
                        "severity": item.severity.value,
                        "score": item.score,
                        "summary": item.summary,
                    }
                    for item in result.evidence
                ],
            }
        )
    canonical = json.dumps(
        semantic_records,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return ReplayReport(
        source=source,
        total_events=len(entries),
        sessions=len(sessions),
        decision_counts=dict(sorted(decision_counts.items())),
        evidence_counts=dict(sorted(evidence_counts.items())),
        compared_events=compared,
        mismatches=mismatches,
        semantic_fingerprint=hashlib.sha256(canonical).hexdigest(),
        entries=tuple(entries),
    )
