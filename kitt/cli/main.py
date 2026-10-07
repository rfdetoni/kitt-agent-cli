from __future__ import annotations

import argparse
import asyncio
import importlib.util
from importlib.metadata import PackageNotFoundError, version as package_version
import os
import sys
from dataclasses import replace
from pathlib import Path

from kitt.core.runtime import KittRuntime
from kitt.core.runtime_config import RuntimeConfig
from kitt.core.logging import configure_logging, debug_event, get_logger
from kitt.history.migrations import IncompatibleSchemaError
from kitt.update_check import notify_if_update_available
from kitt.ui.capabilities import create_backend
from kitt.ui.fallback import HeadlessUI


logger = get_logger(__name__)


def _agent_version() -> str:
    try:
        return package_version("kitt-agent-cli")
    except PackageNotFoundError:
        from kitt import __version__
        return __version__


def _log_runtime_identity() -> None:
    debug_event(
        logger,
        "agent.startup",
        version=_agent_version(),
        package_path=str(Path(__file__).resolve()),
        executable=str(Path(sys.executable).resolve()),
        pid=os.getpid(),
    )


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _missing_companion(feature: str, package: str) -> int:
    print(
        f"{feature} requires the separately owned {package} companion. "
        "Use the K.I.T.T. ecosystem/Agent installer or install that companion package.",
        file=sys.stderr,
    )
    return 2


def _default_log_level() -> int:
    raw = os.getenv("KITT_LOG_LEVEL", "").strip()
    if not raw:
        return 1 if os.getenv("KITT_DEBUG_LOG", "").strip() else 0
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("KITT_LOG_LEVEL must be 0, 1 or 2") from exc
    if value not in {0, 1, 2}:
        raise ValueError("KITT_LOG_LEVEL must be 0, 1 or 2")
    return value


def _configure_debug_log(args) -> Path | None:
    level = int(getattr(args, "log_level", 0))
    requested = str(getattr(args, "log_file", "") or "").strip()
    if not requested:
        requested = os.getenv("KITT_LOG_FILE", "").strip() or os.getenv("KITT_DEBUG_LOG", "").strip()
    if level > 0 and not requested:
        requested = str(Path(args.root).resolve() / ".kitt" / "logs" / "agent-cli.log")

    path = configure_logging(level=level, path=requested or None)
    os.environ["KITT_LOG_LEVEL"] = str(level)
    if path is not None:
        os.environ["KITT_LOG_FILE"] = str(path)
        os.environ["KITT_DEBUG_LOG"] = str(path)
    debug_event(
        logger,
        "cli.logging.configured",
        level=level,
        path=str(path) if path is not None else None,
        root=str(Path(args.root).resolve()),
    )
    return path


def _add_common_options(parser: argparse.ArgumentParser, *, defaults: bool) -> None:
    """Add position-independent CLI options.

    Subparsers suppress defaults so values parsed before the subcommand are not
    overwritten. The root parser owns canonical defaults.
    """

    default = (lambda value: value) if defaults else (lambda _value: argparse.SUPPRESS)
    parser.add_argument("--root", default=default("."), help="Workspace root")
    parser.add_argument(
        "--no-history",
        action="store_true",
        default=default(False),
        help="Disable persistent history/state for this invocation",
    )
    parser.add_argument(
        "--ui",
        choices=["auto", "tui", "plain"],
        default=default("auto"),
    )
    parser.add_argument(
        "--plain",
        action="store_true",
        default=default(False),
        help="Alias for --ui plain",
    )
    parser.add_argument(
        "--no-animation",
        action="store_true",
        default=default(False),
    )
    parser.add_argument(
        "--log-level",
        type=int,
        choices=[0, 1, 2],
        default=default(_default_log_level()),
        help="Logging: 0=normal, 1=debug, 2=full trace",
    )
    parser.add_argument(
        "--log-file",
        default=default(
            os.getenv("KITT_LOG_FILE", "").strip()
            or os.getenv("KITT_DEBUG_LOG", "").strip()
            or None
        ),
        help="Diagnostic log file (default with level>0: <workspace>/.kitt/logs/agent-cli.log)",
    )


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    _add_common_options(common, defaults=False)

    parser = argparse.ArgumentParser(
        prog="kitt",
        description="K.I.T.T. autonomous coding agent",
    )
    _add_common_options(parser, defaults=True)
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {_agent_version()}",
    )
    parser.add_argument("-p", "--print", dest="prompt", help="Print one response and exit")
    parser.add_argument(
        "--contract",
        action="store_true",
        help="Plan and run -p as a durable task contract",
    )
    parser.add_argument(
        "--contract-yes",
        action="store_true",
        help="Start a newly planned contract without a separate confirmation step",
    )
    parser.add_argument(
        "--contract-resume",
        metavar="GOAL_ID",
        default=None,
        help="Resume a blocked or paused contract goal",
    )
    parser.add_argument(
        "--allow",
        dest="contract_allow",
        default=None,
        help="Contract capabilities: comma-separated read,write,run,all",
    )
    parser.add_argument(
        "--contract-max-attempts",
        type=int,
        default=5,
        help="Maximum scheduler attempts per contract item (default: 5)",
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")

    models_parser = subparsers.add_parser(
        "models",
        parents=[common],
        help="List and inspect models from catalog/providers",
    )
    models_parser.add_argument(
        "provider",
        nargs="?",
        default=None,
        help="Provider name (e.g. openai, anthropic, ollama)",
    )
    models_parser.add_argument(
        "--refresh",
        action="store_true",
        help="Force refresh catalog from Models.dev",
    )
    models_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Display full model metadata and capabilities",
    )

    auth_parser = subparsers.add_parser(
        "auth",
        parents=[common],
        help="Manage provider authentication credentials",
    )
    auth_sub = auth_parser.add_subparsers(dest="auth_action", help="Auth action")
    auth_login = auth_sub.add_parser("login", parents=[common], help="Log in to a provider")
    auth_login.add_argument("provider", help="Provider to authenticate (e.g. openai, anthropic)")
    auth_login.add_argument("--method", default="api_key", help="Auth method (api_key, env, session)")
    auth_sub.add_parser("list", parents=[common], help="List authenticated providers")
    auth_logout = auth_sub.add_parser("logout", parents=[common], help="Log out from a provider")
    auth_logout.add_argument("provider", help="Provider to log out")

    plugins_parser = subparsers.add_parser(
        "plugins",
        parents=[common],
        help="Manage plugins and extensions",
    )
    plugins_parser.add_argument(
        "plugin_action",
        nargs="?",
        default="list",
        choices=["list", "inspect", "enable", "disable", "reload", "trust", "untrust"],
        help="Plugin action",
    )
    plugins_parser.add_argument("plugin_name", nargs="?", default=None, help="Target plugin name")

    mcp_parser = subparsers.add_parser(
        "mcp",
        parents=[common],
        help="Manage Model Context Protocol servers",
    )
    mcp_parser.add_argument(
        "mcp_action",
        nargs="?",
        default="list",
        choices=["list", "inspect", "trust", "untrust", "connect", "disconnect", "tools", "resources"],
        help="MCP action",
    )
    mcp_parser.add_argument("server_name", nargs="?", default=None, help="Target MCP server name")

    daemon_parser = subparsers.add_parser(
        "daemon",
        parents=[common],
        help="Manage persistent background KITT Daemon",
    )
    daemon_parser.add_argument(
        "daemon_action",
        nargs="?",
        default="status",
        choices=["start", "run", "stop", "status"],
        help="Daemon action",
    )

    for remote_name in ("remote", "web"):
        remote_parser = subparsers.add_parser(
            remote_name,
            parents=[common],
            help="Serve the KITT web interface for local/private-network control",
        )
        remote_parser.add_argument(
            "--lan",
            action="store_true",
            help="Bind to all interfaces for trusted-LAN access",
        )
        remote_parser.add_argument(
            "--host",
            default=None,
            help="Explicit bind host (default: 127.0.0.1 or 0.0.0.0 with --lan)",
        )
        remote_parser.add_argument(
            "--port",
            type=int,
            default=None,
            help="HTTP port (Control Center/default: 7337; 0 chooses a free port)",
        )
        remote_parser.add_argument(
            "--pairing-ttl",
            type=float,
            default=None,
            help="Pairing-code lifetime in seconds (Control Center/default: 900)",
        )
        remote_parser.add_argument(
            "--session-ttl",
            type=float,
            default=None,
            help="Web-session lifetime in seconds (Control Center/default: 43200)",
        )
        remote_parser.add_argument("--tls-cert", default=None, help="PEM certificate path")
        remote_parser.add_argument("--tls-key", default=None, help="PEM private-key path")

    rpc_parser = subparsers.add_parser(
        "rpc",
        parents=[common],
        help="Serve line-oriented JSON-RPC over the host-owned runtime",
    )
    rpc_parser.add_argument(
        "--no-start-services",
        action="store_true",
        help="Do not start schedulers/extensions while serving RPC",
    )

    sessions_parser = subparsers.add_parser(
        "sessions",
        parents=[common],
        help="List active and saved KITT sessions with runtime state",
    )
    sessions_parser.add_argument("--all", action="store_true", dest="show_all")
    sessions_parser.add_argument("--json", action="store_true", dest="json_output")
    sessions_parser.add_argument("--limit", type=int, default=20)

    incident_parser = subparsers.add_parser(
        "incident",
        parents=[common],
        help="Reconstruct noteworthy runtime events from local structured logs",
    )
    incident_parser.add_argument(
        "--since",
        default="1h",
        help="Duration (30m, 2h, 1d) or ISO timestamp",
    )
    incident_parser.add_argument(
        "--session",
        default=None,
        help="Filter by session/conversation/turn id",
    )
    incident_parser.add_argument("--json", action="store_true", dest="json_output")
    incident_parser.add_argument("--limit", type=int, default=100)

    attach_parser = subparsers.add_parser(
        "attach",
        parents=[common],
        help="Attach to a running KITT session in daemon",
    )
    attach_parser.add_argument("session", help="Session ID or prefix to attach")

    subparsers.add_parser("detach", parents=[common], help="Detach from current daemon session")

    resume_parser = subparsers.add_parser(
        "resume",
        parents=[common],
        help="Resume an existing KITT session",
    )
    resume_parser.add_argument("session", help="Session ID to resume")

    evolve_parser = subparsers.add_parser(
        "evolve",
        parents=[common],
        help="Offline staged self-evolution of managed KITT skills",
    )
    evolve_sub = evolve_parser.add_subparsers(
        dest="evolve_action",
        help="Evolution action",
    )

    evolve_skill = evolve_sub.add_parser(
        "skill",
        parents=[common],
        help="Evolve one managed skill and stage only if holdout improves",
    )
    evolve_skill.add_argument("skill_name", help="Managed skill name")
    evolve_skill.add_argument(
        "--source",
        choices=["synthetic", "history", "golden"],
        default="synthetic",
        help="Evaluation dataset source",
    )
    evolve_skill.add_argument(
        "--dataset",
        default=None,
        help="Golden JSONL path when --source golden",
    )
    evolve_skill.add_argument("--generations", type=int, default=1)
    evolve_skill.add_argument("--population", type=int, default=2)
    evolve_skill.add_argument(
        "--max-calls",
        type=int,
        default=48,
        help="Hard LLM-call budget for the complete evolution run",
    )
    evolve_skill.add_argument(
        "--cases",
        type=int,
        default=10,
        help="Synthetic case count (6-24)",
    )

    evolve_runs = evolve_sub.add_parser(
        "runs",
        parents=[common],
        help="List recent evolution runs",
    )
    evolve_runs.add_argument("--limit", type=int, default=30)

    evolve_show = evolve_sub.add_parser(
        "show",
        parents=[common],
        help="Inspect one evolution run and candidates",
    )
    evolve_show.add_argument("run_id")

    evolve_promote = evolve_sub.add_parser(
        "promote",
        parents=[common],
        help="Explicitly promote a STAGED candidate",
    )
    evolve_promote.add_argument("run_id")

    evolve_reject = evolve_sub.add_parser(
        "reject",
        parents=[common],
        help="Reject a staged/unpromoted evolution run",
    )
    evolve_reject.add_argument("run_id")

    evolve_opportunities = evolve_sub.add_parser(
        "opportunities",
        parents=[common],
        help="Mine Dreaming memory and recent failures for evolution opportunities",
    )
    evolve_opportunities.add_argument("--limit", type=int, default=10)

    learn_parser = subparsers.add_parser(
        "learn",
        parents=[common],
        help="Inspect local optimization evidence without auto-applying changes",
    )
    learn_sub = learn_parser.add_subparsers(
        dest="learn_action",
        help="Learning action",
    )
    learn_sub.add_parser(
        "suggest",
        parents=[common],
        help="Show evidence-backed optimization candidates",
    )
    learn_experiment = learn_sub.add_parser(
        "experiment",
        parents=[common],
        help="Manage local control/candidate measurement windows",
    )
    learn_experiment_sub = learn_experiment.add_subparsers(
        dest="learn_experiment_action",
        help="Experiment action",
    )
    learn_start = learn_experiment_sub.add_parser(
        "start",
        parents=[common],
        help="Start the control arm for a feature",
    )
    learn_start.add_argument("feature")
    learn_switch = learn_experiment_sub.add_parser(
        "switch",
        parents=[common],
        help="Switch an experiment to control or candidate",
    )
    learn_switch.add_argument("feature")
    learn_switch.add_argument("arm", choices=["control", "candidate"])
    learn_report = learn_experiment_sub.add_parser(
        "report",
        parents=[common],
        help="Compare observed experiment arms",
    )
    learn_report.add_argument("feature")

    doctor_parser = subparsers.add_parser(
        "doctor",
        parents=[common],
        help="Run system diagnostics and manage local state",
    )
    doctor_parser.add_argument(
        "--reset-state",
        action="store_true",
        help="Explicitly reset incompatible SQLite database state to Schema V1",
    )
    return parser


async def _run_contract_mode(runtime, args) -> int:
    from kitt.goals.contract_commands import (
        create_planned_contract,
        render_contract,
        resume_contract,
        schedule_contract,
    )

    if args.contract_resume:
        goal = runtime.goals.get(args.contract_resume)
        if goal is None or not runtime.goals.contract_items(goal.id):
            print(f"Contract goal not found: {args.contract_resume}", file=sys.stderr)
            return 2
        resumed = resume_contract(runtime, goal.id)
        if resumed is None:
            print(f"Unable to resume contract: {goal.id}", file=sys.stderr)
            return 2
        goal = resumed
        print(render_contract(runtime, goal.id))
    else:
        if not args.contract or not str(args.prompt or "").strip():
            print("--contract requires -p/--print with a non-empty prompt", file=sys.stderr)
            return 2
        conversation = runtime.history.get_or_create_active()
        try:
            goal, _items = await asyncio.to_thread(
                create_planned_contract,
                runtime,
                conversation_id=conversation["id"],
                objective=args.prompt,
                allow=args.contract_allow,
                max_attempts=max(1, int(args.contract_max_attempts)),
                start_paused=not bool(args.contract_yes),
            )
        except (ValueError, RuntimeError) as exc:
            print(f"Contract planning failed: {exc}", file=sys.stderr)
            return 2
        print(render_contract(runtime, goal.id))
        if not args.contract_yes:
            print(
                f"Contract saved but not started. Review it, then run: "
                f"kitt --contract-resume {goal.id}",
            )
            return 0
        if not schedule_contract(runtime, goal.id):
            print(f"Unable to schedule contract goal {goal.id}", file=sys.stderr)
            return 2

    terminal = {"SUCCEEDED", "FAILED", "CANCELLED", "PAUSED_BUDGET_EXCEEDED"}
    last_state = ""
    while True:
        goal = runtime.goals.get(goal.id)
        if goal is None:
            print("Contract goal disappeared from persistent state.", file=sys.stderr)
            return 2
        if goal.state != last_state:
            print(f"[contract] {goal.id}: {goal.state}")
            last_state = goal.state
        if goal.state in terminal:
            print(render_contract(runtime, goal.id))
            return 0 if goal.state == "SUCCEEDED" else 1
        if goal.state == "WAITING_APPROVAL":
            print(
                "Contract is waiting for a host approval. Approve it from an interactive "
                "KITT session, then use --contract-resume.",
                file=sys.stderr,
            )
            return 3

        results = await asyncio.to_thread(runtime.goal_scheduler.check_and_execute_due)
        for entry in results:
            status = str(entry.get("status") or "")
            contract = entry.get("contract") or {}
            local_id = contract.get("local_id") or ""
            suffix = f" {local_id}" if local_id else ""
            print(f"[contract]{suffix}: {status}")
        if not results:
            await asyncio.sleep(0.1)


async def async_main(args) -> int:
    base_config = RuntimeConfig.from_env()
    persistent = not args.no_history
    resident_available = _module_available("kitt.daemon.client")
    daemon_enabled = bool(base_config.daemon_enabled and resident_available)
    daemon_authoritative = bool(daemon_enabled and persistent)
    contract_mode = bool(
        getattr(args, "contract", False)
        or getattr(args, "contract_resume", None)
    )
    if contract_mode and not persistent:
        print(
            "Contract mode requires persistent state; --no-history is incompatible.",
            file=sys.stderr,
        )
        return 2
    if contract_mode and daemon_authoritative:
        print(
            "Contract mode requires the local runtime to own the GoalScheduler. "
            "Disable the resident daemon for this invocation; no local fallback is used.",
            file=sys.stderr,
        )
        return 2
    config = replace(
        base_config,
        history_enabled=persistent,
        persistence_enabled=persistent,
        daemon_enabled=daemon_enabled,
        frontend_only=daemon_authoritative,
        scheduler_enabled=False if contract_mode else base_config.scheduler_enabled,
    )
    runtime = KittRuntime.build(args.root, config=config)
    # A new KITT invocation starts logically blank. Saved history remains
    # available through explicit history/resume commands.
    runtime.history.begin_fresh_session()
    backend = None
    if not contract_mode:
        backend = (
            HeadlessUI(runtime, args.prompt)
            if args.prompt is not None
            else create_backend(
                runtime,
                "plain" if args.plain else args.ui,
                no_animation=args.no_animation,
            )
        )
    code = 1
    errors = []
    try:
        await runtime.start()
        code = (
            await _run_contract_mode(runtime, args)
            if contract_mode
            else await backend.run_async()
        )
    finally:
        if backend is not None:
            try:
                await backend.shutdown()
            except BaseException as exc:
                errors.append(exc)
        try:
            await runtime.aclose()
        except BaseException as exc:
            errors.append(exc)
    if errors:
        raise RuntimeError("Shutdown failed: " + "; ".join(map(str, errors)))
    return code


async def _run_rpc_mode(args) -> int:
    config = RuntimeConfig.from_env()
    if bool(getattr(args, "no_start_services", False)):
        config = replace(
            config,
            scheduler_enabled=False,
            wake_scheduler_enabled=False,
            frontend_only=False,
        )
    runtime = KittRuntime.build(args.root, config=config)
    try:
        await runtime.start()
        await asyncio.to_thread(runtime.rpc.serve_lines, sys.stdin, sys.stdout)
        return 0
    finally:
        await runtime.aclose()


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_debug_log(args)
    _log_runtime_identity()
    notify_if_update_available(component="agent-cli")

    if args.subcommand == "models":
        from kitt.cli.commands import handle_models_command

        return handle_models_command(
            provider=args.provider,
            refresh=args.refresh,
            verbose=args.verbose,
        )

    if args.subcommand == "auth":
        from kitt.cli.commands import handle_auth_command

        action = getattr(args, "auth_action", "list") or "list"
        return handle_auth_command(
            action=action,
            provider=getattr(args, "provider", None),
            method=getattr(args, "method", "api_key"),
        )

    if args.subcommand == "plugins":
        from kitt.cli.commands import handle_plugins_command

        return handle_plugins_command(
            action=args.plugin_action,
            name=args.plugin_name,
            root_dir=args.root,
        )

    if args.subcommand == "mcp":
        from kitt.cli.commands import handle_mcp_command

        return handle_mcp_command(
            action=args.mcp_action,
            server=args.server_name,
            root_dir=args.root,
        )

    if args.subcommand == "daemon":
        if not _module_available("kitt.daemon.process"):
            return _missing_companion("Daemon management", "kitt-assistant-runtime")
        from kitt.cli.commands import handle_daemon_command

        return handle_daemon_command(action=args.daemon_action, root_dir=args.root)

    if args.subcommand in {"remote", "web"}:
        if not _module_available("kitt.remote.cli"):
            return _missing_companion("Remote/Web control", "kitt-assistant-runtime")
        from kitt.remote.cli import run_remote_command

        return run_remote_command(
            root_dir=args.root,
            lan=bool(args.lan),
            host=args.host,
            port=args.port,
            pairing_ttl=args.pairing_ttl,
            session_ttl=args.session_ttl,
            tls_cert=args.tls_cert,
            tls_key=args.tls_key,
        )

    if args.subcommand == "rpc":
        return asyncio.run(_run_rpc_mode(args))

    if args.subcommand == "sessions":
        from kitt.cli.commands import handle_sessions_command

        return handle_sessions_command(
            root_dir=args.root,
            limit=args.limit,
            show_all=bool(args.show_all),
            json_output=bool(args.json_output),
        )

    if args.subcommand == "incident":
        from kitt.cli.commands import handle_incident_command

        return handle_incident_command(
            root_dir=args.root,
            since=args.since,
            session=args.session,
            limit=args.limit,
            json_output=bool(args.json_output),
        )

    if args.subcommand == "attach":
        if not _module_available("kitt.daemon.client"):
            return _missing_companion("Daemon session attach", "kitt-assistant-runtime")
        from kitt.cli.commands import handle_attach_command

        return handle_attach_command(session_id=args.session, root_dir=args.root)

    if args.subcommand == "detach":
        print("\033[90mDetached from session.\033[0m")
        return 0

    if args.subcommand == "resume":
        if not _module_available("kitt.daemon.client"):
            return _missing_companion("Daemon session resume", "kitt-assistant-runtime")
        from kitt.cli.commands import handle_resume_command

        return handle_resume_command(session_id=args.session, root_dir=args.root)

    if args.subcommand == "evolve":
        if not _module_available("kitt.evolution.cli"):
            return _missing_companion("Self-evolution", "kitt-evolution")
        from kitt.evolution.cli import handle_evolve_command

        return handle_evolve_command(args)

    if args.subcommand == "learn":
        from kitt.cli.commands import handle_learn_command

        return handle_learn_command(args)

    if args.subcommand == "doctor":
        from kitt.cli.commands import handle_doctor_command

        return handle_doctor_command(
            root_dir=args.root,
            reset_state=bool(getattr(args, "reset_state", False)),
        )

    try:
        return asyncio.run(async_main(args))
    except IncompatibleSchemaError as exc:
        print(f"K.I.T.T. state schema error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
