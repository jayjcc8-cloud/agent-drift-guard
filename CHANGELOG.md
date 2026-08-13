# Changelog

This project follows semantic versioning for the Python package and documents operator-visible changes here.

## Unreleased

## 0.7.1 - 2026-08-13

- Enforce `0700` on private Hook data and backup directories and `0600` on anchors and
  configuration backups, including repair of existing installations.
- Make `hook-status` verify handlers, the executable runner, anchors, and private permissions, and
  return a non-zero status for degraded installations.
- Resolve nested Codex project runners relative to the enclosing Git root and reject non-Git Codex
  installation targets instead of installing a command that cannot run.
- Count anchors, `.gitignore`, and permission repairs as installation changes, and write replay
  reports atomically with `0600` permissions.
- Recognize Python standard-library `unittest` commands in generated default anchors and migrate the
  exact legacy generated default, preventing verified Codex sessions from being blocked at Stop.

## 0.7.0 - 2026-08-09

- Add eight privacy-reviewed, real controlled Codex 0.147.0 and Claude Code 2.1.98 replay cases with
  forty manually labeled events across clean, repeated-failure, failed-validation, subagent, and
  compaction scenarios.
- Enforce corpus provenance, minimization, privacy, deterministic action/semantic replay, exact label
  match, clean false-positive, positive recall, and cross-platform parity in CI.
- Normalize plain-text Codex and stream-only Claude unittest results from their deterministic terminal
  status lines, eliminating clean-session validation false positives without broad output heuristics.
- Ignore Claude's display-only Bash `description` field in LoopDetector fingerprints so changing attempt
  labels cannot hide repeated identical execution.
- Publish baseline and corrected Detector quality metrics while deferring statistical thresholds until
  the public corpus reaches at least fifty labeled events.

## 0.6.0 - 2026-08-09

- Stream replay input and write replay data atomically, expose truncated SQLite exports, and compare full deterministic
  decision/evidence semantics in addition to actions.
- Evaluate manually labeled replay cases with exact match, clean false-positive rate, precision, recall, and F1;
  make mismatch gating cover action, semantic, and label regressions.
- Rotate bounded JSONL telemetry, persist exporter failure counters, and expose `telemetry-status`.
- Cover Codex/Claude `PermissionRequest` plus Claude `TaskCompleted` and `StopFailure` in native Adapter
  contract tests and project Hook installation; emit Event Protocol v0.2 while retaining v0.1 replay reads.
- Keep machine-specific Hook runners and Claude settings private while leaving shareable config free of
  absolute executable and project paths.
- Apply the default 30-day/5000-event retention policy automatically at most once per day and migrate
  SQLite stores to schema v3.
- Include telemetry writes in the default cold-process benchmark and validate both real local CLIs.

## 0.5.0 - 2026-08-09

- Added sanitized long-session observation capture and SQLite session replay export.
- Added deterministic replay reports, semantic fingerprints, and decision mismatch exit status.
- Added JSONL observability envelopes and best-effort runtime exporting.
- Added idempotent install, status, backup, and uninstall commands for Codex and Claude Code Hooks.
- Added open-source governance files, CI, security guidance, and public roadmap.

## 0.4.0 - 2026-08-08

- Added secure-by-default event redaction and configurable retention cleanup.
- Added transactional SQLite schema migration from v1 to v2.
- Added full cold-process Hook latency benchmarking.
- Kept the local daemon optional after Codex and Claude Code p95 remained under 75 ms locally.

## 0.3.0 - 2026-08-08

- Added SQLite WAL persistence, atomic session sequencing, durable decisions, and Hook CLI integration.

## 0.2.0 - 2026-08-08

- Added real Codex and Claude Code Adapters, Supervisor, and deterministic Detectors.

## 0.1.0 - 2026-08-08

- Added platform-neutral event, decision, and capability protocol contracts.
