from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import replace

from kitt.core.runtime import KittRuntime
from kitt.core.runtime_config import RuntimeConfig
from kitt.ui.capabilities import create_backend
from kitt.ui.fallback import HeadlessUI


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


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    _add_common_options(common, defaults=False)

    parser = argparse.ArgumentParser(
        prog="kitt",
        description="K.I.T.T. autonomous coding agent",
    )
    _add_common_options(parser, defaults=True)
    parser.add_argument("-p", "--print", dest="prompt", help="Print one response and exit")
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
            default=7337,
            help="HTTP port (default: 7337; 0 chooses a free port)",
        )
        remote_parser.add_argument(
            "--pairing-ttl",
            type=float,
            default=900.0,
            help="Pairing-code lifetime in seconds",
        )
        remote_parser.add_argument(
            "--session-ttl",
            type=float,
            default=43_200.0,
            help="Web-session lifetime in seconds",
        )
        remote_parser.add_argument("--tls-cert", default=None, help="PEM certificate path")
        remote_parser.add_argument("--tls-key", default=None, help="PEM private-key path")

    subparsers.add_parser("sessions", parents=[common], help="List active and saved KITT sessions")

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


async def async_main(args) -> int:
    base_config = RuntimeConfig.from_env()
    persistent = not args.no_history
    daemon_authoritative = bool(base_config.daemon_enabled and persistent)
    config = replace(
        base_config,
        history_enabled=persistent,
        persistence_enabled=persistent,
        frontend_only=daemon_authoritative,
    )
    runtime = KittRuntime.build(args.root, config=config)
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
        code = await backend.run_async()
    finally:
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


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

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
        from kitt.cli.commands import handle_daemon_command

        return handle_daemon_command(action=args.daemon_action, root_dir=args.root)

    if args.subcommand in {"remote", "web"}:
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

    if args.subcommand == "sessions":
        from kitt.cli.commands import handle_sessions_command

        return handle_sessions_command(root_dir=args.root)

    if args.subcommand == "attach":
        from kitt.cli.commands import handle_attach_command

        return handle_attach_command(session_id=args.session, root_dir=args.root)

    if args.subcommand == "detach":
        print("\033[90mDetached from session.\033[0m")
        return 0

    if args.subcommand == "resume":
        from kitt.cli.commands import handle_resume_command

        return handle_resume_command(session_id=args.session, root_dir=args.root)

    if args.subcommand == "doctor":
        from kitt.cli.commands import handle_doctor_command

        return handle_doctor_command(
            root_dir=args.root,
            reset_state=bool(getattr(args, "reset_state", False)),
        )

    try:
        return asyncio.run(async_main(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
