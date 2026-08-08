# Roadmap

## v0.5 — Runtime operations (complete)

- Sanitized long-session capture and deterministic replay reports.
- JSONL observability exporter with stable result envelopes.
- Idempotent project-level Hook install, status, and uninstall for Codex and Claude Code.

## v0.6 — Integrations (next)

- OTLP/HTTP export after the telemetry envelope is exercised in real sessions.
- Optional Unix socket daemon only when target-machine p95 is materially over budget.
- One-command diagnostics bundle with privacy review.

## Later

- LLM judge as an optional second opinion, never the sole enforcement path.
- Additional platform Adapters and conformance fixtures.
- Signed release automation and reproducible build attestations.
