"""Deterministic replay of sanitized long-session observations."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import Counter
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from pydantic import Field

from agent_drift.core import GuardAnchors, SupervisionResult, Supervisor
from agent_drift.observability import ObservationEnvelope
from agent_drift.protocol.base import WireModel
from agent_drift.protocol.decisions import DecisionAction, DriftType
from agent_drift.protocol.events import AgentEvent, EventType
from agent_drift.store.base import EventStore


class ReplayCase(WireModel):
    event: AgentEvent
    expected_action: DecisionAction | None = None
    expected_semantic_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    expected_drift_types: tuple[DriftType, ...] | None = None


class ReplayEntry(WireModel):
    index: int = Field(ge=0)
    event_id: str
    session_id: str
    event_type: EventType
    expected_action: DecisionAction | None = None
    actual_action: DecisionAction
    matches_expected: bool | None = None
    semantic_matches_expected: bool | None = None
    expected_drift_types: tuple[DriftType, ...] | None = None
    drift_types_match_expected: bool | None = None
    drift_types: tuple[DriftType, ...] = ()
    detectors: tuple[str, ...] = ()


class ReplayQuality(WireModel):
    labeled_events: int = Field(ge=0)
    exact_matches: int = Field(ge=0)
    label_mismatches: int = Field(ge=0)
    exact_match_rate: float | None = Field(default=None, ge=0, le=1)
    clean_events: int = Field(ge=0)
    clean_false_positive_events: int = Field(ge=0)
    clean_false_positive_rate: float | None = Field(default=None, ge=0, le=1)
    true_positives: int = Field(ge=0)
    false_positives: int = Field(ge=0)
    false_negatives: int = Field(ge=0)
    precision: float | None = Field(default=None, ge=0, le=1)
    recall: float | None = Field(default=None, ge=0, le=1)
    f1: float | None = Field(default=None, ge=0, le=1)


class ReplayReport(WireModel):
    schema_version: str = "0.2"
    source: str
    anchors_fingerprint: str
    history_limit: int = Field(ge=1)
    protocol_versions: tuple[str, ...]
    total_events: int
    sessions: int
    decision_counts: dict[str, int]
    evidence_counts: dict[str, int]
    compared_events: int
    mismatches: int
    semantic_compared_events: int
    semantic_mismatches: int
    quality: ReplayQuality
    semantic_fingerprint: str
    entries: tuple[ReplayEntry, ...]


class ReplayExportResult(WireModel):
    session_id: str
    output_path: str
    events: int
    total_session_events: int
    truncated: bool
    first_sequence: int | None = None
    last_sequence: int | None = None
    expected_decisions: int


def _semantic_projection(result: SupervisionResult) -> dict[str, Any]:
    decision = result.decision
    return {
        "event_type": result.event.event_type.value,
        "decision": {
            "action": decision.action.value,
            "fallback_action": (
                decision.fallback_action.value if decision.fallback_action is not None else None
            ),
            "severity": decision.severity.value,
            "drift_type": decision.drift_type.value if decision.drift_type is not None else None,
            "score": decision.score,
            "confidence": decision.confidence,
            "reason": decision.reason,
            "context": decision.context,
        },
        "evidence": [
            {
                "detector": item.detector,
                "drift_type": item.drift_type.value,
                "severity": item.severity.value,
                "score": item.score,
                "summary": item.summary,
                "facts": item.facts,
            }
            for item in result.evidence
        ],
    }


def _semantic_fingerprint(result: SupervisionResult) -> str:
    canonical = json.dumps(
        _semantic_projection(result),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def iter_replay_cases(path: str | Path) -> Iterator[ReplayCase]:
    source = Path(path)
    found = False
    with source.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            found = True
            try:
                document: Any = json.loads(line)
                if isinstance(document, dict) and "supervision" in document:
                    observation = ObservationEnvelope.model_validate(document)
                    yield ReplayCase(
                        event=observation.supervision.event,
                        expected_action=observation.supervision.decision.action,
                        expected_semantic_fingerprint=_semantic_fingerprint(
                            observation.supervision
                        ),
                    )
                elif isinstance(document, dict) and "event" in document:
                    yield ReplayCase.model_validate(document)
                else:
                    yield ReplayCase(event=AgentEvent.model_validate(document))
            except (json.JSONDecodeError, ValueError) as exc:
                raise ValueError(f"invalid replay record at line {line_number}: {exc}") from exc
    if not found:
        raise ValueError("replay input contains no events")


def load_replay_cases(path: str | Path) -> tuple[ReplayCase, ...]:
    """Load replay cases eagerly for API compatibility; CLI uses the streaming iterator."""

    return tuple(iter_replay_cases(path))


def write_replay_cases(cases: Iterable[ReplayCase], path: str | Path) -> int:
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    temporary = Path(temporary_name)
    count = 0
    try:
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            for case in cases:
                stream.write(case.model_dump_json(exclude_none=True))
                stream.write("\n")
                count += 1
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        if os.name != "nt":
            os.chmod(destination, 0o600)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return count


def export_store_session(
    store: EventStore,
    session_id: str,
    path: str | Path,
    *,
    limit: int = 5000,
) -> ReplayExportResult:
    if limit < 1:
        raise ValueError("replay export limit must be positive")
    total = store.count_session_events(session_id)
    events = store.load_history(session_id, limit=limit)
    cases: list[ReplayCase] = []
    expected = 0
    for event in events:
        result = store.get_result(event.event_id)
        action = result.decision.action if result is not None else None
        if action is not None:
            expected += 1
        cases.append(
            ReplayCase(
                event=event,
                expected_action=action,
                expected_semantic_fingerprint=(
                    _semantic_fingerprint(result) if result is not None else None
                ),
            )
        )
    output_path = str(Path(path).expanduser().resolve())
    count = write_replay_cases(cases, output_path)
    return ReplayExportResult(
        session_id=session_id,
        output_path=output_path,
        events=count,
        total_session_events=total,
        truncated=total > count,
        first_sequence=events[0].sequence if events else None,
        last_sequence=events[-1].sequence if events else None,
        expected_decisions=expected,
    )


def run_replay(
    cases: Iterable[ReplayCase],
    anchors: GuardAnchors,
    *,
    source: str = "memory",
    history_limit: int = 500,
    include_entries: bool = True,
) -> ReplayReport:
    supervisor = Supervisor(anchors, history_limit=history_limit)
    entries: list[ReplayEntry] = []
    decision_counts: Counter[str] = Counter()
    evidence_counts: Counter[str] = Counter()
    fingerprint = hashlib.sha256()
    sessions: set[str] = set()
    protocol_versions: set[str] = set()
    compared = 0
    mismatches = 0
    semantic_compared = 0
    semantic_mismatches = 0
    labeled_events = 0
    exact_matches = 0
    clean_events = 0
    clean_false_positive_events = 0
    true_positives = 0
    false_positives = 0
    false_negatives = 0
    total_events = 0
    for index, case in enumerate(cases):
        total_events += 1
        result = supervisor.process(case.event)
        action = result.decision.action
        decision_counts[action.value] += 1
        sessions.add(result.event.session_id)
        protocol_versions.add(str(result.event.protocol_version))
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
        actual_semantic_fingerprint = _semantic_fingerprint(result)
        semantic_matches: bool | None = None
        if case.expected_semantic_fingerprint is not None:
            semantic_compared += 1
            semantic_matches = actual_semantic_fingerprint == case.expected_semantic_fingerprint
            if not semantic_matches:
                semantic_mismatches += 1
        label_matches: bool | None = None
        if case.expected_drift_types is not None:
            labeled_events += 1
            expected_types = set(case.expected_drift_types)
            actual_types = set(drift_types)
            label_matches = expected_types == actual_types
            if label_matches:
                exact_matches += 1
            if not expected_types:
                clean_events += 1
                if actual_types:
                    clean_false_positive_events += 1
            true_positives += len(expected_types & actual_types)
            false_positives += len(actual_types - expected_types)
            false_negatives += len(expected_types - actual_types)
        if include_entries:
            entries.append(
                ReplayEntry(
                    index=index,
                    event_id=str(result.event.event_id),
                    session_id=result.event.session_id,
                    event_type=result.event.event_type,
                    expected_action=case.expected_action,
                    actual_action=action,
                    matches_expected=matches,
                    semantic_matches_expected=semantic_matches,
                    expected_drift_types=case.expected_drift_types,
                    drift_types_match_expected=label_matches,
                    drift_types=drift_types,
                    detectors=detectors,
                )
            )
        fingerprint.update(index.to_bytes(8, byteorder="big", signed=False))
        fingerprint.update(bytes.fromhex(actual_semantic_fingerprint))
    if total_events == 0:
        raise ValueError("replay input contains no events")
    anchors_canonical = anchors.model_dump_json(exclude_none=True).encode("utf-8")
    precision = (
        true_positives / (true_positives + false_positives)
        if true_positives + false_positives
        else None
    )
    recall = (
        true_positives / (true_positives + false_negatives)
        if true_positives + false_negatives
        else None
    )
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall
        else None
    )
    return ReplayReport(
        source=source,
        anchors_fingerprint=hashlib.sha256(anchors_canonical).hexdigest(),
        history_limit=history_limit,
        protocol_versions=tuple(sorted(protocol_versions)),
        total_events=total_events,
        sessions=len(sessions),
        decision_counts=dict(sorted(decision_counts.items())),
        evidence_counts=dict(sorted(evidence_counts.items())),
        compared_events=compared,
        mismatches=mismatches,
        semantic_compared_events=semantic_compared,
        semantic_mismatches=semantic_mismatches,
        quality=ReplayQuality(
            labeled_events=labeled_events,
            exact_matches=exact_matches,
            label_mismatches=labeled_events - exact_matches,
            exact_match_rate=exact_matches / labeled_events if labeled_events else None,
            clean_events=clean_events,
            clean_false_positive_events=clean_false_positive_events,
            clean_false_positive_rate=(
                clean_false_positive_events / clean_events if clean_events else None
            ),
            true_positives=true_positives,
            false_positives=false_positives,
            false_negatives=false_negatives,
            precision=precision,
            recall=recall,
            f1=f1,
        ),
        semantic_fingerprint=fingerprint.hexdigest(),
        entries=tuple(entries),
    )
