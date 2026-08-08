# Changelog

This project follows semantic versioning for the Python package and documents operator-visible changes here.

## Unreleased

- Long-session replay pipeline.
- Observability exporters.
- Idempotent Codex and Claude Code Hook installer.

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
