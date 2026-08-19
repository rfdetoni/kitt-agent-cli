"""CLI commands for models catalog and provider authentication."""
from __future__ import annotations

import getpass
import sys
from typing import Optional

from kitt.llm.auth import ProviderAuthService
from kitt.llm.catalog import ProviderCatalogService
from kitt.llm.registry import ProviderRegistry


def handle_models_command(
    provider: Optional[str] = None,
    refresh: bool = False,
    verbose: bool = False,
    registry: Optional[ProviderRegistry] = None,
) -> int:
    reg = registry or ProviderRegistry()
    if refresh:
        print("[Catalog] Refreshing models catalog from Models.dev...")
        success = reg.catalog.refresh(force=True)
        if success:
            print("\033[32m✓ Catalog refreshed successfully!\033[0m")
        else:
            print("\033[33m⚠ Offline or refresh failed; using cached/builtin catalog.\033[0m")

    if provider:
        pid = provider.strip().lower()
        p = reg.get_provider(pid)
        if not p:
            print(f"\033[31mError: Unknown provider '{provider}'. Run 'kitt models' to list all.\033[0m")
            return 1

        models = reg.effective_models(pid)
        if not models:
            # Try runtime discovery
            res = reg.discover_runtime_models(pid, timeout=4.0)
            models = res.models

        if not models:
            print(f"\033[90mNo models discovered for provider '{pid}'.\033[0m")
            return 0

        print(f"\n\033[1;36m=== Models for {p.name} ({pid}) ===\033[0m")
        for m in models:
            if verbose:
                tools_badge = "\033[32m[tools]\033[0m" if m.supports_tools else "\033[90m[no-tools]\033[0m"
                reason_badge = "\033[33m[reasoning]\033[0m" if m.supports_reasoning else ""
                print(f"  \033[1m{pid}/{m.id}\033[0m {tools_badge} {reason_badge}")
                print(f"    Name           : {m.name}")
                print(f"    Context Window : {m.context_window or 'N/A'}")
                print(f"    Max Output     : {m.max_output_tokens or 'N/A'}")
                print(f"    Modalities     : {','.join(m.input_modalities)} -> {','.join(m.output_modalities)}")
            else:
                print(f"{pid}/{m.id}")
        return 0

    # List all providers and their models
    providers = reg.list_providers()
    print("\n\033[1;36m=== Available Providers & Models ===\033[0m")
    for p in providers:
        models = reg.effective_models(p.id)
        print(f"\n\033[1;33m[{p.name}] ({p.id})\033[0m — Protocol: {p.protocol}")
        if not models:
            print("  \033[90m(Run 'kitt models " + p.id + "' to discover runtime models)\033[0m")
            continue
        for m in models[:12]:
            if verbose:
                tools_badge = " [tools]" if m.supports_tools else ""
                print(f"  {p.id}/{m.id} (ctx: {m.context_window}){tools_badge}")
            else:
                print(f"  {p.id}/{m.id}")
        if len(models) > 12:
            print(f"  \033[90m... and {len(models) - 12} more models (run 'kitt models {p.id}' for full list)\033[0m")
    return 0


def handle_auth_command(
    action: str,
    provider: Optional[str] = None,
    method: str = "api_key",
    secret: Optional[str] = None,
    auth_service: Optional[ProviderAuthService] = None,
) -> int:
    auth = auth_service or ProviderAuthService()
    action = (action or "list").strip().lower()

    if action == "list":
        states = auth.authenticated()
        print("\n\033[1;36m=== Authenticated Providers ===\033[0m")
        if not states:
            print("\033[90mNo active provider credentials configured.\033[0m")
            print("To log in to a provider: \033[1mkitt auth login <provider>\033[0m")
            return 0

        for s in states:
            print(f"  • \033[1;32m{s.provider_id}\033[0m (Method: {s.auth_type}, Ref: {s.credential_ref})")
        return 0

    if action == "login":
        if not provider:
            print("\033[31mError: Missing provider name. Usage: kitt auth login <provider>\033[0m")
            return 1
        pid = provider.strip().lower()

        val = secret
        if not val:
            try:
                val = getpass.getpass(f"Enter API Key / Token for '{pid}': ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nLogin cancelled.")
                return 1

        if not val:
            print("\033[31mError: Empty secret provided.\033[0m")
            return 1

        state = auth.login(pid, val, method=method)
        print(f"\033[32m✓ Successfully authenticated {pid}! (Saved to CredentialStore as {state.credential_ref})\033[0m")
        return 0

    if action == "logout":
        if not provider:
            print("\033[31mError: Missing provider name. Usage: kitt auth logout <provider>\033[0m")
            return 1
        pid = provider.strip().lower()
        auth.logout(pid)
        print(f"\033[32m✓ Successfully logged out from {pid}.\033[0m")
        return 0

    print(f"\033[31mUnknown auth action '{action}'. Choices: list, login, logout.\033[0m")
    return 1


def handle_plugins_command(
    action: str = "list",
    name: Optional[str] = None,
    root_dir: str = ".",
) -> int:
    import asyncio
    from kitt.extensions.manager import ExtensionManager
    ext = ExtensionManager(workspace_root=root_dir)
    manifests = ext.plugins.discover()
    action = (action or "list").strip().lower()

    if action == "list":
        print("\n\033[1;36m=== Discovered & Installed Plugins ===\033[0m")
        if not manifests:
            print("\033[90mNo plugins found in workspace (.kitt/plugins/) or global (~/.kitt/plugins/).\033[0m")
            return 0

        for p_name, m in manifests.items():
            status = "\033[32m[enabled]\033[0m" if m.enabled_by_default else "\033[90m[disabled]\033[0m"
            print(f"  • \033[1m{m.name}\033[0m v{m.version} ({m.source}) {status}")
            if m.description:
                print(f"    Description: {m.description}")
            if m.permissions:
                print(f"    Permissions: {', '.join(sorted(m.permissions))}")
        return 0

    if action == "inspect":
        if not name:
            print("\033[31mError: Missing plugin name. Usage: kitt plugins inspect <name>\033[0m")
            return 1
        pid = name.strip().lower()
        m = manifests.get(pid)
        if not m:
            print(f"\033[31mError: Plugin '{pid}' not found.\033[0m")
            return 1
        print(f"\n\033[1;36m=== Plugin: {m.name} ===\033[0m")
        print(f"  Version      : {m.version}")
        print(f"  API Version  : {m.api_version}")
        print(f"  Entrypoint   : {m.entrypoint}")
        print(f"  Source       : {m.source}")
        print(f"  Path         : {m.manifest_path}")
        print(f"  Author       : {m.author or 'N/A'}")
        print(f"  Permissions  : {', '.join(sorted(m.permissions)) or 'None'}")
        print(f"  Dependencies : {', '.join(m.dependencies) or 'None'}")
        return 0

    if action == "enable":
        if not name:
            print("\033[31mError: Missing plugin name. Usage: kitt plugins enable <name>\033[0m")
            return 1
        ext.plugins.enable(name)
        print(f"\033[32m✓ Plugin '{name}' enabled.\033[0m")
        return 0

    if action == "disable":
        if not name:
            print("\033[31mError: Missing plugin name. Usage: kitt plugins disable <name>\033[0m")
            return 1
        ext.plugins.disable(name)
        print(f"\033[32m✓ Plugin '{name}' disabled.\033[0m")
        return 0

    print(f"\033[31mUnknown plugins action '{action}'. Choices: list, inspect, enable, disable.\033[0m")
    return 1


def handle_mcp_command(
    action: str = "list",
    server: Optional[str] = None,
    root_dir: str = ".",
) -> int:
    import asyncio
    from kitt.extensions.manager import ExtensionManager
    ext = ExtensionManager(workspace_root=root_dir)
    action = (action or "list").strip().lower()

    if action == "list":
        servers = ext.mcp.list_servers()
        print("\n\033[1;36m=== Configured MCP Servers ===\033[0m")
        if not servers:
            print("\033[90mNo MCP servers configured in ~/.kitt/config/mcp.json or .kitt/mcp.json.\033[0m")
            return 0

        for s in servers:
            status = ext.mcp.get_server_status(s.server_id).value
            print(f"  • \033[1m{s.server_id}\033[0m (Transport: {s.transport}, Status: {status}, Trust: {s.trust})")
            if s.command:
                print(f"    Command: {s.command} {' '.join(s.args)}")
        return 0

    if action in ("tools", "resources"):
        async def _inspect():
            if server:
                await ext.mcp.connect(server)
            else:
                await ext.mcp.connect_all_enabled()

            if action == "tools":
                tools = ext.mcp.list_tools(server)
                print(f"\n\033[1;36m=== MCP Tools ({len(tools)}) ===\033[0m")
                for t in tools:
                    print(f"  • \033[1m{t.full_name}\033[0m: {t.description}")
            else:
                print(f"\n\033[1;36m=== MCP Resources ===\033[0m")
                # List resources if server connected
                pass
            await ext.mcp.disconnect_all()

        asyncio.run(_inspect())
        return 0

    print(f"\033[31mUnknown MCP action '{action}'. Choices: list, tools, resources.\033[0m")
    return 1


def handle_daemon_command(action: str = "status", root_dir: str = ".") -> int:
    import asyncio
    from kitt.daemon.client import DaemonClient
    from kitt.daemon.server import DaemonServer

    client = DaemonClient()
    action = (action or "status").strip().lower()

    if action == "start":
        async def _start():
            if await client.is_running():
                print("\033[33mKITT Daemon is already running.\033[0m")
                return 0
            server = DaemonServer(workspace_root=root_dir)
            await server.start()
            print("\033[32m✓ KITT Daemon started successfully.\033[0m")
            try:
                while True:
                    await asyncio.sleep(1)
            except (KeyboardInterrupt, asyncio.CancelledError):
                await server.stop()
                print("\n\033[90mKITT Daemon stopped.\033[0m")
            return 0

        return asyncio.run(_start())

    elif action == "stop":
        async def _stop():
            if not await client.connect():
                print("\033[90mKITT Daemon is not running.\033[0m")
                return 0
            await client.stop_daemon()
            await client.close()
            print("\033[32m✓ KITT Daemon stopped.\033[0m")
            return 0

        return asyncio.run(_stop())

    elif action == "status":
        async def _status():
            running = await client.is_running()
            if running:
                print("\033[32m● KITT Daemon: RUNNING\033[0m")
                print(f"  Socket: {client.socket_path}")
            else:
                print("\033[90m○ KITT Daemon: STOPPED\033[0m")
            await client.close()
            return 0

        return asyncio.run(_status())

    print(f"\033[31mUnknown daemon action '{action}'. Choices: start, stop, status.\033[0m")
    return 1


def handle_sessions_command(root_dir: str = ".") -> int:
    import asyncio
    from kitt.daemon.client import DaemonClient

    async def _sessions():
        client = DaemonClient()
        if not await client.connect():
            # Fallback to local database
            from kitt.core.runtime import KittRuntime
            from kitt.core.runtime_config import RuntimeConfig
            rt = KittRuntime.build(root_dir, config=RuntimeConfig())
            convs = rt.history.list_conversations(limit=20)
            active = rt.history.get_active_read_only()
            active_id = active["id"] if active else ""
            print("\n\033[1;36m=== KITT Sessions (Local) ===\033[0m")
            for c in convs:
                tag = "\033[32m[ACTIVE]\033[0m" if c.id == active_id else ""
                print(f"  • \033[1m{c.id[:12]}\033[0m {tag} {c.title}")
            rt.close()
            return 0

        resp = await client.list_sessions(workspace=root_dir)
        sessions = resp.get("sessions", [])
        active_id = resp.get("active_session_id", "")
        print("\n\033[1;36m=== KITT Daemon Sessions ===\033[0m")
        for s in sessions:
            tag = "\033[32m[ACTIVE]\033[0m" if s["id"] == active_id else ""
            print(f"  • \033[1m{s['id'][:12]}\033[0m {tag} {s['title']} ({s['status']})")
        await client.close()
        return 0

    return asyncio.run(_sessions())


def handle_attach_command(session_id: str, root_dir: str = ".") -> int:
    import asyncio
    from kitt.daemon.client import DaemonClient

    async def _attach():
        client = DaemonClient()
        if not await client.connect():
            print("\033[31mError: KITT Daemon is not running. Start it with 'kitt daemon start'\033[0m")
            return 1

        def on_event(evt):
            print(f"[{evt.event_type}] {evt.payload}")

        res = await client.attach(session_id, on_event=on_event, workspace=root_dir)
        if res.get("status") == "ok":
            print(f"\033[32m✓ Attached to session {session_id}. Replaying past events:\033[0m")
            for e in res.get("events", []):
                print(f"  [{e.get('event_type')}] {e.get('payload')}")
            print("\033[90mListening for events... Press Ctrl+C to detach.\033[0m")
            try:
                while True:
                    await asyncio.sleep(1)
            except (KeyboardInterrupt, asyncio.CancelledError):
                await client.detach()
                print("\n\033[90mDetached from session.\033[0m")
        else:
            print(f"\033[31mFailed to attach: {res.get('error')}\033[0m")
        await client.close()
        return 0

    return asyncio.run(_attach())


def handle_resume_command(session_id: str, root_dir: str = ".") -> int:
    # Resuming switches active session and initiates turn
    return handle_attach_command(session_id, root_dir=root_dir)

