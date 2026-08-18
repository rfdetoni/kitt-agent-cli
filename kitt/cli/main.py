from __future__ import annotations

import argparse
import asyncio
import sys

from kitt.core.runtime import KittRuntime
from kitt.core.runtime_config import RuntimeConfig
from kitt.ui.capabilities import create_backend
from kitt.ui.fallback import HeadlessUI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kitt", description="K.I.T.T. autonomous coding agent")
    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")

    # 'models' subcommand
    models_parser = subparsers.add_parser("models", help="List and inspect models from catalog/providers")
    models_parser.add_argument("provider", nargs="?", default=None, help="Provider name (e.g. openai, anthropic, ollama)")
    models_parser.add_argument("--refresh", action="store_true", help="Force refresh catalog from Models.dev")
    models_parser.add_argument("-v", "--verbose", action="store_true", help="Display full model metadata and capabilities")

    # 'auth' subcommand
    auth_parser = subparsers.add_parser("auth", help="Manage provider authentication credentials")
    auth_sub = auth_parser.add_subparsers(dest="auth_action", help="Auth action")

    auth_login = auth_sub.add_parser("login", help="Log in to a provider")
    auth_login.add_argument("provider", help="Provider to authenticate (e.g. openai, anthropic)")
    auth_login.add_argument("--method", default="api_key", help="Auth method (api_key, env, session)")

    auth_sub.add_parser("list", help="List authenticated providers")

    auth_logout = auth_sub.add_parser("logout", help="Log out from a provider")
    auth_logout.add_argument("provider", help="Provider to log out")

    # 'plugins' subcommand
    plugins_parser = subparsers.add_parser("plugins", help="Manage plugins and extensions")
    plugins_parser.add_argument("plugin_action", nargs="?", default="list", choices=["list", "inspect", "enable", "disable", "reload"], help="Plugin action")
    plugins_parser.add_argument("plugin_name", nargs="?", default=None, help="Target plugin name")

    # 'mcp' subcommand
    mcp_parser = subparsers.add_parser("mcp", help="Manage Model Context Protocol servers")
    mcp_parser.add_argument("mcp_action", nargs="?", default="list", choices=["list", "inspect", "connect", "disconnect", "tools", "resources"], help="MCP action")
    mcp_parser.add_argument("server_name", nargs="?", default=None, help="Target MCP server name")

    # Default flags
    parser.add_argument("-p", "--print", dest="prompt", help="Print one response and exit")
    parser.add_argument("--root", default=".", help="Workspace root")
    parser.add_argument("--no-history", action="store_true")
    parser.add_argument("--ui", choices=["auto", "tui", "plain"], default="auto")
    parser.add_argument("--plain", action="store_true", help="Alias for --ui plain")
    parser.add_argument("--no-animation", action="store_true")
    return parser


async def async_main(args) -> int:
    config = RuntimeConfig(history_enabled=not args.no_history, persistence_enabled=not args.no_history)
    runtime = KittRuntime.build(args.root, config=config)
    backend = HeadlessUI(runtime, args.prompt) if args.prompt is not None else create_backend(
        runtime, "plain" if args.plain else args.ui, no_animation=args.no_animation,
    )
    code = 1
    errors = []
    try:
        code = await backend.run_async()
    finally:
        try: await backend.shutdown()
        except BaseException as exc: errors.append(exc)
        try: runtime.close()
        except BaseException as exc: errors.append(exc)
    if errors:
        raise RuntimeError("Shutdown failed: " + "; ".join(map(str, errors)))
    return code


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.subcommand == "models":
        from kitt.cli.commands import handle_models_command
        return handle_models_command(provider=args.provider, refresh=args.refresh, verbose=args.verbose)

    if args.subcommand == "auth":
        from kitt.cli.commands import handle_auth_command
        action = getattr(args, "auth_action", "list") or "list"
        provider = getattr(args, "provider", None)
        method = getattr(args, "method", "api_key")
        return handle_auth_command(action=action, provider=provider, method=method)

    if args.subcommand == "plugins":
        from kitt.cli.commands import handle_plugins_command
        return handle_plugins_command(action=args.plugin_action, name=args.plugin_name, root_dir=getattr(args, "root", "."))

    if args.subcommand == "mcp":
        from kitt.cli.commands import handle_mcp_command
        return handle_mcp_command(action=args.mcp_action, server=args.server_name, root_dir=getattr(args, "root", "."))

    try:
        return asyncio.run(async_main(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
