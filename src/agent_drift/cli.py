"""Small JSON CLI for adapters and protocol conformance checks."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agent_drift.adapters import ClaudeCodeAdapter, CodexAdapter, PlatformAdapter
from agent_drift.benchmark import run_hook_benchmark
from agent_drift.core import GuardAnchors, Supervisor
from agent_drift.installer import HookInstaller, HookInstallError
from agent_drift.observability import JsonlExporter
from agent_drift.protocol.capabilities import PlatformCapabilities
from agent_drift.protocol.decisions import GuardDecision
from agent_drift.protocol.events import AgentEvent
from agent_drift.replay import export_store_session, iter_replay_cases, run_replay
from agent_drift.runtime import AgentDriftRuntime
from agent_drift.store import RedactionPolicy, RetentionPolicy, SQLiteStore


def _read_json(path: str) -> Any:
    text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    return json.loads(text)


def _write_model(model: Any) -> None:
    print(model.model_dump_json(indent=2, exclude_none=True))


def _write_private_text(path: str | Path, text: str) -> None:
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        if os.name != "nt":
            os.chmod(destination, 0o600)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


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

    store_export = subparsers.add_parser(
        "store-export-replay", help="export one sanitized SQLite session as replay JSONL"
    )
    store_export.add_argument("database")
    store_export.add_argument("session_id")
    store_export.add_argument("output")
    store_export.add_argument("--limit", type=int, default=5000)

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
    hook.add_argument(
        "--mode",
        choices=("enforce", "observe"),
        default="enforce",
        help=(
            "host action mode; omitted mode preserves the legacy enforcing behavior, "
            "while observe records proposed decisions without intervening"
        ),
    )
    hook.add_argument("--redaction-policy", help="optional RedactionPolicy JSON file")
    hook.add_argument("--telemetry-jsonl", help="append sanitized observations as JSONL")
    hook.add_argument("--telemetry-max-bytes", type=int, default=32 * 1024 * 1024)
    hook.add_argument("--telemetry-backups", type=int, default=3)
    hook.add_argument("--telemetry-max-record-bytes", type=int, default=1024 * 1024)

    telemetry_status = subparsers.add_parser(
        "telemetry-status", help="inspect local JSONL exporter size and failure health"
    )
    telemetry_status.add_argument("path")
    telemetry_status.add_argument("--backup-count", type=int, default=3)

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
    benchmark.add_argument(
        "--no-telemetry", action="store_true", help="exclude JSONL export from the benchmark"
    )

    replay = subparsers.add_parser(
        "replay", help="re-run sanitized long-session observations deterministically"
    )
    replay.add_argument("path", help="AgentEvent or observation JSONL path")
    replay.add_argument("--anchors", required=True)
    replay.add_argument("--output", help="optional report JSON path")
    replay.add_argument("--summary-only", action="store_true")
    replay.add_argument(
        "--assume-clean",
        action="store_true",
        help="label every replay event as expected to have no drift evidence",
    )
    replay.add_argument("--fail-on-mismatch", action="store_true")

    for name, action in (
        ("install-hooks", "install"),
        ("hook-status", "status"),
        ("uninstall-hooks", "uninstall"),
    ):
        installer = subparsers.add_parser(name, help=f"{action} project-level Agent Drift hooks")
        installer.add_argument("platform", choices=("codex", "claude-code"))
        installer.add_argument("--project-root", default=".")
        installer.add_argument("--executable")
        if name == "install-hooks":
            installer.add_argument("--anchors")
            installer.add_argument(
                "--mode",
                choices=("enforce", "observe"),
                help=(
                    "explicit runtime mode; new installs default to observe, while an "
                    "existing installation keeps its detected effective mode"
                ),
            )
        if name != "hook-status":
            installer.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command in {"store-init", "store-stats"}:
            store = SQLiteStore(args.database, retention_policy=None)
            output = store.stats().model_dump(mode="json")
            output["integrity"] = store.integrity_check()
            print(json.dumps(output, ensure_ascii=False, indent=2))
        elif args.command == "store-events":
            store = SQLiteStore(args.database, retention_policy=None)
            events = store.load_history(args.session_id, limit=args.limit)
            print(
                json.dumps(
                    [event.model_dump(mode="json", exclude_none=True) for event in events],
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "store-export-replay":
            export_result = export_store_session(
                SQLiteStore(args.database, retention_policy=None),
                args.session_id,
                args.output,
                limit=args.limit,
            )
            _write_model(export_result)
        elif args.command == "store-prune":
            max_age = None if args.no_age_limit else timedelta(days=args.max_age_days)
            max_events = None if args.no_count_limit else args.max_events_per_session
            prune_result = SQLiteStore(args.database, retention_policy=None).prune(
                RetentionPolicy(
                    max_age=max_age,
                    max_events_per_session=max_events,
                ),
                dry_run=not args.apply,
            )
            _write_model(prune_result)
        elif args.command == "hook":
            try:
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
                    exporter=(
                        JsonlExporter(
                            args.telemetry_jsonl,
                            max_bytes=args.telemetry_max_bytes,
                            backup_count=args.telemetry_backups,
                            max_record_bytes=args.telemetry_max_record_bytes,
                        )
                        if args.telemetry_jsonl
                        else None
                    ),
                    mode=args.mode,
                )
                outcome = runtime.handle(
                    _read_json(args.path),
                    repo_root=args.repo_root,
                )
            except (
                OSError,
                json.JSONDecodeError,
                ValidationError,
                ValueError,
                RuntimeError,
            ):
                if args.mode != "observe":
                    raise
                print("agent-drift: observation unavailable", file=sys.stderr)
                return 0
            if outcome.response.stdout is not None:
                print(json.dumps(outcome.response.stdout, ensure_ascii=False))
            if outcome.response.stderr:
                print(outcome.response.stderr, file=sys.stderr)
            if outcome.export_error:
                message = (
                    "agent-drift: observation export failed"
                    if args.mode == "observe"
                    else f"agent-drift: telemetry export failed: {outcome.export_error}"
                )
                print(message, file=sys.stderr)
            return outcome.response.exit_code
        elif args.command == "telemetry-status":
            _write_model(JsonlExporter(args.path, backup_count=args.backup_count).status())
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
                include_telemetry=not args.no_telemetry,
            )
            _write_model(benchmark_result)
        elif args.command == "replay":
            anchors = GuardAnchors.model_validate(_read_json(args.anchors))
            cases = iter_replay_cases(args.path)
            if args.assume_clean:
                cases = (case.model_copy(update={"expected_drift_types": ()}) for case in cases)
            replay_report = run_replay(
                cases,
                anchors,
                source=str(Path(args.path).resolve()),
                include_entries=not args.summary_only,
            )
            document = replay_report.model_dump(mode="json", exclude_none=True)
            if args.summary_only:
                document.pop("entries", None)
            replay_output = json.dumps(document, ensure_ascii=False, indent=2)
            if args.output:
                _write_private_text(args.output, replay_output + "\n")
            else:
                print(replay_output)
            if args.fail_on_mismatch and (
                replay_report.mismatches
                or replay_report.semantic_mismatches
                or replay_report.quality.label_mismatches
            ):
                return 1
        elif args.command in {"install-hooks", "hook-status", "uninstall-hooks"}:
            executable = args.executable
            current_entrypoint = Path(sys.argv[0]).resolve()
            if (
                executable is None
                and current_entrypoint.name == "agent-drift"
                and current_entrypoint.is_file()
            ):
                executable = str(current_entrypoint)
            installer = HookInstaller(
                args.platform,
                args.project_root,
                executable=executable,
            )
            if args.command == "install-hooks":
                install_result = installer.install(
                    anchors=args.anchors,
                    dry_run=args.dry_run,
                    mode=args.mode,
                )
            elif args.command == "uninstall-hooks":
                install_result = installer.uninstall(dry_run=args.dry_run)
            else:
                install_result = installer.status()
            _write_model(install_result)
            if args.command == "hook-status" and install_result.healthy is False:
                return 1
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
    except (
        OSError,
        json.JSONDecodeError,
        ValidationError,
        ValueError,
        RuntimeError,
        HookInstallError,
    ) as exc:
        print(f"agent-drift: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
