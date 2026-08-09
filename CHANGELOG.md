# Changelog

This project follows semantic versioning for the Python package and documents operator-visible changes here.

## Unreleased

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
