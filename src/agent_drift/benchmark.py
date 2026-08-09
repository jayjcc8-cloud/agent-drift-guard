"""End-to-end command Hook latency benchmark."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from statistics import fmean, median
from time import perf_counter_ns

from pydantic import Field

from agent_drift.protocol.base import WireModel


class LatencySummary(WireModel):
    minimum_ms: float = Field(ge=0)
    mean_ms: float = Field(ge=0)
    median_ms: float = Field(ge=0)
    p95_ms: float = Field(ge=0)
    maximum_ms: float = Field(ge=0)


class HookBenchmarkResult(WireModel):
    platform: str
    telemetry_enabled: bool
    iterations: int = Field(ge=1)
    warmup_iterations: int = Field(ge=0)
    budget_ms: float = Field(gt=0)
    budget_exceeded: bool
    latency: LatencySummary


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def summarize_latency(samples_ms: list[float]) -> LatencySummary:
    if not samples_ms:
        raise ValueError("at least one latency sample is required")
    return LatencySummary(
        minimum_ms=min(samples_ms),
        mean_ms=fmean(samples_ms),
        median_ms=median(samples_ms),
        p95_ms=_percentile(samples_ms, 0.95),
        maximum_ms=max(samples_ms),
    )


def run_hook_benchmark(
    *,
    platform: str,
    hook_path: str | Path,
    anchors_path: str | Path,
    database_path: str | Path | None = None,
    repo_root: str | None = None,
    redaction_policy_path: str | Path | None = None,
    iterations: int = 30,
    warmup_iterations: int = 3,
    budget_ms: float = 75.0,
    include_telemetry: bool = True,
) -> HookBenchmarkResult:
    if iterations < 1 or warmup_iterations < 0 or budget_ms <= 0:
        raise ValueError("invalid benchmark iterations or latency budget")
    source_document = json.loads(Path(hook_path).read_text(encoding="utf-8"))
    if not isinstance(source_document, dict):
        raise ValueError("native hook fixture must be a JSON object")
    base_session = str(source_document.get("session_id", "benchmark"))
    package_root = str(Path(__file__).resolve().parents[1])
    environment = os.environ.copy()
    existing_python_path = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        package_root
        if not existing_python_path
        else os.pathsep.join((package_root, existing_python_path))
    )
    samples_ms: list[float] = []
    with tempfile.TemporaryDirectory(prefix="agent-drift-benchmark-") as directory:
        temporary_root = Path(directory)
        database = Path(database_path) if database_path else temporary_root / "benchmark.db"
        telemetry = temporary_root / "observations.jsonl"
        total = warmup_iterations + iterations
        for index in range(total):
            document = dict(source_document)
            document["session_id"] = f"{base_session}-benchmark-{index}"
            iteration_hook = temporary_root / f"hook-{index}.json"
            iteration_hook.write_text(json.dumps(document), encoding="utf-8")
            command = [
                sys.executable,
                "-m",
                "agent_drift.cli",
                "hook",
                platform,
                str(iteration_hook),
                "--database",
                str(database),
                "--anchors",
                str(Path(anchors_path).resolve()),
            ]
            if repo_root:
                command.extend(("--repo-root", repo_root))
            if redaction_policy_path:
                command.extend(("--redaction-policy", str(Path(redaction_policy_path).resolve())))
            if include_telemetry:
                command.extend(("--telemetry-jsonl", str(telemetry)))
            started = perf_counter_ns()
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            elapsed_ms = (perf_counter_ns() - started) / 1_000_000
            if completed.returncode != 0:
                detail = completed.stderr.strip() or completed.stdout.strip()
                raise RuntimeError(
                    f"benchmark hook failed with exit {completed.returncode}: {detail}"
                )
            if index >= warmup_iterations:
                samples_ms.append(elapsed_ms)
    latency = summarize_latency(samples_ms)
    return HookBenchmarkResult(
        platform=platform,
        telemetry_enabled=include_telemetry,
        iterations=iterations,
        warmup_iterations=warmup_iterations,
        budget_ms=budget_ms,
        budget_exceeded=latency.p95_ms > budget_ms,
        latency=latency,
    )
