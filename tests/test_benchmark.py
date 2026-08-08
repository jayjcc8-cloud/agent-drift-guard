from __future__ import annotations

import pytest

from agent_drift.benchmark import summarize_latency


def test_latency_summary_uses_interpolated_p95() -> None:
    summary = summarize_latency([10.0, 20.0, 30.0, 40.0, 50.0])
    assert summary.minimum_ms == 10.0
    assert summary.mean_ms == 30.0
    assert summary.median_ms == 30.0
    assert summary.p95_ms == pytest.approx(48.0)
    assert summary.maximum_ms == 50.0


def test_latency_summary_requires_samples() -> None:
    with pytest.raises(ValueError, match="at least one"):
        summarize_latency([])
