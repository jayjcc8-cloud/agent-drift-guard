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

## v0.7 — Real-session Detector evidence gate (complete)

- Eight privacy-reviewed real controlled sessions: four Codex and four Claude Code scenarios.
- Forty manually labeled minimized events with deterministic action and semantic regression gates.
- Evidence-backed normalization for unittest results and LoopDetector fingerprints.
- 100% exact match, 0% clean false-positive rate, and no missed expected positives.
- No new integration surface: OTLP, diagnostics, daemon, Adapters, LLM Judge, and DriftTypes remain out of scope.

## v0.7.1 — Codex production hardening (complete)

- Repair private Hook directory and backup permissions on existing installations.
- Report degraded Hook installations instead of configuration-only false positives.
- Make Codex Git-root resolution, installer mutation reporting, and replay report privacy explicit.
- Require a real Codex install/status/session/uninstall verification before release.

## v0.8 — Observability integration (proposed)

- OTLP/HTTP export only after the v0.7 evidence gate remains stable.
- Exporter retry, backpressure, and failure visibility without changing guard decisions.
- One-command diagnostics bundle only with an explicit privacy contract.
- Optional Unix socket daemon only when target-machine p95 is materially over budget.

## Later

- LLM judge as an optional second opinion, never the sole enforcement path.
- Additional platform Adapters and conformance fixtures.
- Signed release automation and reproducible build attestations.
