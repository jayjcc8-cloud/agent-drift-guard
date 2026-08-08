"""Small JSON CLI for adapters and protocol conformance checks."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agent_drift.adapters import ClaudeCodeAdapter, CodexAdapter, PlatformAdapter
from agent_drift.benchmark import run_hook_benchmark
from agent_drift.core import GuardAnchors, Supervisor
from agent_drift.protocol.capabilities import PlatformCapabilities
from agent_drift.protocol.decisions import GuardDecision
from agent_drift.protocol.events import AgentEvent
from agent_drift.runtime import AgentDriftRuntime
from agent_drift.store import RedactionPolicy, RetentionPolicy, SQLiteStore


def _read_json(path: str) -> Any:
    text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    return json.loads(text)


def _write_model(model: Any) -> None:
    print(model.model_dump_json(indent=2, exclude_none=True))


def _adapter(platform: str, platform_version: str | None = None) -> PlatformAdapter:
    if platform == "codex":
        return CodexAdapter(platform_version)
    if platform == "claude-code":
        return ClaudeCodeAdapter(platform_version)
    raise ValueError(f"unsupported adapter platform {platform!r}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-drift")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (
        ("validate-event", "validate and normalize an AgentEvent JSON document"),
        ("validate-decision", "validate and normalize a GuardDecision JSON document"),
        ("capabilities", "assess a PlatformCapabilities JSON document"),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument("path", help="JSON file path, or '-' for stdin")

    adapt = subparsers.add_parser(
        "adapt-hook", help="normalize a native Codex or Claude Code hook document"
    )
    adapt.add_argument("platform", choices=("codex", "claude-code"))
    adapt.add_argument("path", help="native hook JSON file path, or '-' for stdin")
    adapt.add_argument("--repo-root")
    adapt.add_argument("--platform-version")

    render = subparsers.add_parser(
        "render-hook", help="translate a GuardDecision into a native hook response"
    )
    render.add_argument("platform", choices=("codex", "claude-code"))
    render.add_argument("event_path", help="normalized AgentEvent JSON path")
    render.add_argument("decision_path", help="GuardDecision JSON path")
    render.add_argument("--platform-version")

    adapter_capabilities = subparsers.add_parser(
        "adapter-capabilities", help="show capabilities advertised by a built-in adapter"
    )
    adapter_capabilities.add_argument("platform", choices=("codex", "claude-code"))
    adapter_capabilities.add_argument("--platform-version")

    store_init = subparsers.add_parser("store-init", help="initialize a SQLite event store")
    store_init.add_argument("database")

    store_stats = subparsers.add_parser("store-stats", help="inspect SQLite store health")
    store_stats.add_argument("database")

    store_events = subparsers.add_parser("store-events", help="read normalized session events")
    store_events.add_argument("database")
    store_events.add_argument("session_id")
    store_events.add_argument("--limit", type=int, default=100)

    store_prune = subparsers.add_parser(
        "store-prune", help="preview or apply event retention cleanup"
    )
    store_prune.add_argument("database")
    store_prune.add_argument("--max-age-days", type=float, default=30.0)
    store_prune.add_argument("--max-events-per-session", type=int, default=5000)
    store_prune.add_argument("--no-age-limit", action="store_true")
    store_prune.add_argument("--no-count-limit", action="store_true")
    store_prune.add_argument(
        "--apply", action="store_true", help="delete matching data; default is dry-run"
    )

    hook = subparsers.add_parser(
        "hook", help="run one native hook through durable supervision and emit native output"
    )
    hook.add_argument("platform", choices=("codex", "claude-code"))
    hook.add_argument("path", nargs="?", default="-", help="native hook JSON path or stdin")
    hook.add_argument("--database", required=True)
    hook.add_argument("--anchors", required=True)
    hook.add_argument("--repo-root")
    hook.add_argument("--platform-version")
    hook.add_argument("--redaction-policy", help="optional RedactionPolicy JSON file")

    benchmark = subparsers.add_parser(
        "benchmark-hook", help="measure full command Hook process latency"
    )
    benchmark.add_argument("platform", choices=("codex", "claude-code"))
    benchmark.add_argument("path", help="native hook fixture JSON path")
    benchmark.add_argument("--anchors", required=True)
    benchmark.add_argument("--database")
    benchmark.add_argument("--repo-root")
    benchmark.add_argument("--redaction-policy")
    benchmark.add_argument("--iterations", type=int, default=30)
    benchmark.add_argument("--warmup", type=int, default=3)
    benchmark.add_argument("--budget-ms", type=float, default=75.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command in {"store-init", "store-stats"}:
            store = SQLiteStore(args.database)
            output = store.stats().model_dump(mode="json")
            output["integrity"] = store.integrity_check()
            print(json.dumps(output, ensure_ascii=False, indent=2))
        elif args.command == "store-events":
            store = SQLiteStore(args.database)
            events = store.load_history(args.session_id, limit=args.limit)
            print(
                json.dumps(
                    [event.model_dump(mode="json", exclude_none=True) for event in events],
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "store-prune":
            max_age = None if args.no_age_limit else timedelta(days=args.max_age_days)
            max_events = None if args.no_count_limit else args.max_events_per_session
            prune_result = SQLiteStore(args.database).prune(
                RetentionPolicy(
                    max_age=max_age,
                    max_events_per_session=max_events,
                ),
                dry_run=not args.apply,
            )
            _write_model(prune_result)
        elif args.command == "hook":
            adapter = _adapter(args.platform, args.platform_version)
            anchors = GuardAnchors.model_validate(_read_json(args.anchors))
            redaction_policy = (
                RedactionPolicy.model_validate(_read_json(args.redaction_policy))
                if args.redaction_policy
                else RedactionPolicy()
            )
            runtime = AgentDriftRuntime(
                adapter,
                Supervisor(
                    anchors,
                    store=SQLiteStore(args.database, redaction_policy=redaction_policy),
                ),
            )
            outcome = runtime.handle(
                _read_json(args.path),
                repo_root=args.repo_root,
            )
            if outcome.response.stdout is not None:
                print(json.dumps(outcome.response.stdout, ensure_ascii=False))
            if outcome.response.stderr:
                print(outcome.response.stderr, file=sys.stderr)
            return outcome.response.exit_code
        elif args.command == "benchmark-hook":
            benchmark_result = run_hook_benchmark(
                platform=args.platform,
                hook_path=args.path,
                anchors_path=args.anchors,
                database_path=args.database,
                repo_root=args.repo_root,
                redaction_policy_path=args.redaction_policy,
                iterations=args.iterations,
                warmup_iterations=args.warmup,
                budget_ms=args.budget_ms,
            )
            _write_model(benchmark_result)
        elif args.command == "adapter-capabilities":
            _write_model(_adapter(args.platform, args.platform_version).capabilities)
        elif args.command == "adapt-hook":
            document = _read_json(args.path)
            event = _adapter(args.platform, args.platform_version).adapt_event(
                document, repo_root=args.repo_root
            )
            _write_model(event)
        elif args.command == "render-hook":
            event = AgentEvent.model_validate(_read_json(args.event_path))
            decision = GuardDecision.model_validate(_read_json(args.decision_path))
            response = _adapter(args.platform, args.platform_version).render_decision(
                event, decision
            )
            _write_model(response)
        elif args.command == "validate-event":
            document = _read_json(args.path)
            event = AgentEvent.model_validate(document)
            event.assert_supported()
            _write_model(event)
        elif args.command == "validate-decision":
            document = _read_json(args.path)
            decision = GuardDecision.model_validate(document)
            decision.assert_supported()
            _write_model(decision)
        else:
            document = _read_json(args.path)
            capabilities = PlatformCapabilities.model_validate(document)
            capabilities.assert_supported()
            output = capabilities.model_dump(mode="json", exclude_none=True)
            output["assessment"] = capabilities.assess().model_dump(mode="json")
            print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except (OSError, json.JSONDecodeError, ValidationError, ValueError, RuntimeError) as exc:
        print(f"agent-drift: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
