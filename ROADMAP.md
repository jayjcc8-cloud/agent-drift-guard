# Roadmap

## v0.5 — Runtime operations (complete)

- Sanitized long-session capture and deterministic replay reports.
- JSONL observability exporter with stable result envelopes.
- Idempotent project-level Hook install, status, and uninstall for Codex and Claude Code.

## v0.6 — Reliability and integration (complete)

- Quality-gated sanitized real-session replay and deterministic semantic regression checks.
- Bounded replay/JSONL storage, automatic retention, exporter health and schema v3 migration.
- Codex/Claude protocol matrix coverage for permission, task completion and terminal failure events.
- Private, project-local Hook runners with shareable configuration free of machine paths.

## v0.7 — Evidence-led integrations

- Expand privacy-reviewed, drift-positive real-session fixtures before adding new integrations.
- OTLP/HTTP export only after telemetry semantics pass the real-session quality gate.
- One-command diagnostics bundle only with an explicit privacy contract.
- Optional Unix socket daemon only when target-machine p95 is materially over budget.

## Later

- LLM judge as an optional second opinion, never the sole enforcement path.
- Additional platform Adapters and conformance fixtures.
- Signed release automation and reproducible build attestations.
