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
    from kitt.daemon.client import DaemonClient
    from kitt.extensions.manager import ExtensionManager

    ext = ExtensionManager(workspace_root=root_dir)
    manifests = ext.plugins.discover()
    action = (action or "list").strip().lower()

    async def _daemon_request(ipc_action: str, params=None):
        client = DaemonClient(workspace_root=root_dir)
        try:
            if not await client.is_running():
                return None
            payload = {"workspace": root_dir}
            if params:
                payload.update(params)
            return await client.send_request(ipc_action, payload)
        finally:
            await client.close()

    if action == "list":
        print("\n\033[1;36m=== Discovered Plugins ===\033[0m")
        if not manifests:
            print("\033[90mNo plugins found.\033[0m")
            return 0
        for plugin_id, manifest in manifests.items():
            enabled = ext.plugins.is_enabled(plugin_id, manifest)
            trusted = ext.plugin_trust.is_trusted(manifest)
            state = "enabled" if enabled else "disabled"
            trust = "trusted" if trusted else "untrusted"
            print(
                f"  • \033[1m{manifest.name}\033[0m v{manifest.version} "
                f"({manifest.source}) [{state}] [{trust}]"
            )
        return 0

    if not name:
        print(f"\033[31mError: Missing plugin name for action '{action}'.\033[0m")
        return 1
    plugin_id = name.strip().lower()
    manifest = manifests.get(plugin_id)
    if not manifest:
        print(f"\033[31mError: Plugin '{plugin_id}' not found.\033[0m")
        return 1

    if action == "inspect":
        trust = ext.plugin_trust.status(manifest)
        print(f"\n\033[1;36m=== Plugin: {manifest.name} ===\033[0m")
        print(f"  Version      : {manifest.version}")
        print(f"  API Version  : {manifest.api_version}")
        print(f"  Entrypoint   : {manifest.entrypoint}")
        print(f"  Source       : {manifest.source}")
        print(f"  Path         : {manifest.manifest_path}")
        print(f"  Permissions  : {', '.join(sorted(manifest.permissions)) or 'None'}")
        print(f"  Enabled      : {ext.plugins.is_enabled(plugin_id, manifest)}")
        print(f"  User trusted : {trust['trusted']}")
        print(f"  Content SHA  : {trust['digest']}")
        return 0

    if action == "enable":
        daemon_res = asyncio.run(
            _daemon_request("plugin.enable", {"name": plugin_id})
        )
        if daemon_res is not None:
            if daemon_res.get("status") != "ok":
                print(f"\033[31mPlugin enable failed: {daemon_res.get('error')}\033[0m")
                return 1
            print(f"\033[32m✓ Plugin '{plugin_id}' enabled.\033[0m")
            return 0
        ext.plugins.enable(plugin_id)
        print(f"\033[32m✓ Plugin '{plugin_id}' enabled in local workspace state.\033[0m")
        return 0
    if action == "disable":
        daemon_res = asyncio.run(
            _daemon_request("plugin.disable", {"name": plugin_id})
        )
        if daemon_res is not None:
            if daemon_res.get("status") != "ok":
                print(f"\033[31mPlugin disable failed: {daemon_res.get('error')}\033[0m")
                return 1
            print(f"\033[32m✓ Plugin '{plugin_id}' disabled.\033[0m")
            return 0
        ext.plugins.disable(plugin_id)
        print(f"\033[32m✓ Plugin '{plugin_id}' disabled in local workspace state.\033[0m")
        return 0
    if action == "trust":
        try:
            digest = ext.plugin_trust.grant(manifest)
        except Exception as exc:
            print(f"\033[31mTrust grant failed: {exc}\033[0m")
            return 1
        print(f"\033[32m✓ Trusted '{plugin_id}' for exact content hash {digest[:16]}…\033[0m")
        return 0
    if action == "untrust":
        removed = ext.plugin_trust.revoke(plugin_id)
        print(
            f"\033[32m✓ Trust revoked for '{plugin_id}'.\033[0m"
            if removed else f"\033[90mPlugin '{plugin_id}' had no trust grant.\033[0m"
        )
        return 0
    if action == "reload":
        daemon_res = asyncio.run(
            _daemon_request("plugin.reload", {"name": plugin_id})
        )
        if daemon_res is not None:
            if daemon_res.get("status") != "ok":
                print(f"\033[31mPlugin reload failed: {daemon_res.get('error')}\033[0m")
                return 1
            print(f"\033[32m✓ Plugin '{plugin_id}' reloaded in active daemon.\033[0m")
            return 0
        async def _reload():
            await ext.plugins.reload(plugin_id)
            await ext.plugins.unload(plugin_id)
        try:
            asyncio.run(_reload())
        except Exception as exc:
            print(f"\033[31mPlugin reload validation failed: {exc}\033[0m")
            return 1
        print(
            f"\033[32m✓ Plugin '{plugin_id}' reload validated. "
            "Restart long-running KITT/daemon runtimes to pick up disk changes.\033[0m"
        )
        return 0

    print(f"\033[31mUnknown plugins action '{action}'.\033[0m")
    return 1


def handle_mcp_command(
    action: str = "list",
    server: Optional[str] = None,
    root_dir: str = ".",
) -> int:
    import asyncio
    from kitt.daemon.client import DaemonClient
    from kitt.extensions.manager import ExtensionManager

    ext = ExtensionManager(workspace_root=root_dir)
    action = (action or "list").strip().lower()
    servers = {cfg.server_id: cfg for cfg in ext.mcp.list_servers()}

    async def _daemon_request(ipc_action: str, params=None):
        client = DaemonClient(workspace_root=root_dir)
        try:
            if not await client.is_running():
                return None
            payload = {"workspace": root_dir}
            if params:
                payload.update(params)
            return await client.send_request(ipc_action, payload)
        finally:
            await client.close()

    if action == "list":
        print("\n\033[1;36m=== Configured MCP Servers ===\033[0m")
        if not servers:
            print("\033[90mNo MCP servers configured.\033[0m")
            return 0
        for server_id, cfg in servers.items():
            status = ext.mcp.get_server_status(server_id).value
            print(
                f"  • \033[1m{server_id}\033[0m "
                f"(Transport: {cfg.transport}, Status: {status}, Enabled: {cfg.enabled}, Trust: {cfg.trust})"
            )
        return 0

    if action == "inspect":
        if not server:
            print("\033[31mError: Missing MCP server name.\033[0m")
            return 1
        cfg = servers.get(server.strip().lower())
        if not cfg:
            print(f"\033[31mError: MCP server '{server}' not found.\033[0m")
            return 1
        print(f"\n\033[1;36m=== MCP: {cfg.server_id} ===\033[0m")
        print(f"  Transport : {cfg.transport}")
        print(f"  Command   : {cfg.command or 'N/A'}")
        print(f"  Args      : {' '.join(cfg.args)}")
        print(f"  Enabled   : {cfg.enabled}")
        print(f"  Trust     : {cfg.trust}")
        print(f"  Timeout   : {cfg.timeout_seconds}s")
        print(f"  Allow     : {cfg.allow_tools or 'all'}")
        print(f"  Deny      : {cfg.deny_tools or 'none'}")
        return 0

    if action == "disconnect":
        daemon_res = asyncio.run(
            _daemon_request(
                "mcp.disconnect",
                {"server_id": server.strip().lower() if server else ""},
            )
        )
        if daemon_res is not None:
            if daemon_res.get("status") != "ok":
                print(f"\033[31mMCP disconnect failed: {daemon_res.get('error')}\033[0m")
                return 1
            print(f"\033[32m✓ MCP '{server}' disconnected.\033[0m")
            return 0
        print(
            "\033[90mStandalone CLI owns no persistent MCP transport. "
            "The long-running KITT runtime/daemon owns connections and closes them on shutdown.\033[0m"
        )
        return 0

    if action in {"connect", "tools", "resources"}:
        ipc_action = {
            "connect": "mcp.connect",
            "tools": "mcp.tools",
            "resources": "mcp.resources",
        }[action]
        daemon_res = asyncio.run(
            _daemon_request(
                ipc_action,
                {"server_id": server.strip().lower() if server else ""},
            )
        )
        if daemon_res is not None:
            if daemon_res.get("status") != "ok":
                print(f"\033[31mMCP {action} failed: {daemon_res.get('error')}\033[0m")
                return 1
            if action == "connect":
                print(
                    f"\033[32m✓ MCP '{daemon_res.get('server_id')}' connected; "
                    f"{daemon_res.get('tools', 0)} tool(s) visible.\033[0m"
                )
                return 0
            if action == "tools":
                tools = daemon_res.get("tools", [])
                print(f"\n\033[1;36m=== MCP Tools ({len(tools)}) ===\033[0m")
                for tool in tools:
                    print(f"  • \033[1m{tool.get('full_name')}\033[0m: {tool.get('description', '')}")
                return 0
            resources = daemon_res.get("resources", [])
            print(f"\n\033[1;36m=== MCP Resources ({len(resources)}) ===\033[0m")
            for resource in resources:
                print(
                    f"  • \033[1m{resource.get('name')}\033[0m "
                    f"[{resource.get('server_id')}] {resource.get('uri')}"
                )
            return 0

    async def _inspect_connected():
        clients = []
        try:
            if server:
                clients.append(await ext.mcp.connect(server))
            else:
                for cfg in ext.mcp.list_servers():
                    if cfg.enabled and (cfg.command or cfg.url):
                        clients.append(await ext.mcp.connect(cfg.server_id))
            if action == "connect":
                if not clients:
                    raise RuntimeError("No MCP server selected/eligible")
                for client in clients:
                    tools = await client.list_tools()
                    print(
                        f"\033[32m✓ MCP '{client.config.server_id}' connected; "
                        f"{len(tools)} tool(s) visible.\033[0m"
                    )
                return 0
            if action == "tools":
                tools = ext.mcp.list_tools(server)
                print(f"\n\033[1;36m=== MCP Tools ({len(tools)}) ===\033[0m")
                for tool in tools:
                    print(f"  • \033[1m{tool.full_name}\033[0m: {tool.description}")
                return 0
            if action == "resources":
                resources = []
                for client in clients:
                    resources.extend(await client.list_resources())
                print(f"\n\033[1;36m=== MCP Resources ({len(resources)}) ===\033[0m")
                for resource in resources:
                    print(f"  • \033[1m{resource.name}\033[0m [{resource.server_id}] {resource.uri}")
                return 0
            return 1
        finally:
            await ext.mcp.disconnect_all()

    if action in {"connect", "tools", "resources"}:
        try:
            return asyncio.run(_inspect_connected())
        except Exception as exc:
            print(f"\033[31mMCP {action} failed: {exc}\033[0m")
            return 1

    print(f"\033[31mUnknown MCP action '{action}'.\033[0m")
    return 1


def handle_daemon_command(action: str = "status", root_dir: str = ".") -> int:
    from kitt.daemon.process import (
        start_daemon_detached,
        run_daemon_foreground,
        stop_daemon,
        get_daemon_status,
    )

    action = (action or "status").strip().lower()

    if action == "start":
        res = start_daemon_detached(workspace=root_dir)
        if res.get("status") == "ok":
            print(f"\033[32m✓ {res.get('message')}\033[0m")
            return 0
        else:
            print(f"\033[31mError: {res.get('error')}\033[0m")
            return 1

    elif action == "run":
        run_daemon_foreground(workspace=root_dir)
        return 0

    elif action == "stop":
        res = stop_daemon(workspace=root_dir)
        if res.get("status") == "ok":
            print(f"\033[32m✓ {res.get('message')}\033[0m")
            return 0
        else:
            print(f"\033[90m{res.get('error')}\033[0m")
            return 0

    elif action == "status":
        info = get_daemon_status(workspace=root_dir)
        if info["running"]:
            print(f"\033[32m● KITT Daemon: RUNNING (PID {info['pid']})\033[0m")
            print(f"  Transport: {info['transport']} -> {info['address']}{':' + str(info['port']) if info['port'] else ''}")
        else:
            print("\033[90m○ KITT Daemon: STOPPED\033[0m")
        return 0

    print(f"\033[31mUnknown daemon action '{action}'. Choices: start, run, stop, status.\033[0m")
    return 1


def handle_sessions_command(root_dir: str = ".") -> int:
    import asyncio
    from kitt.daemon.client import DaemonClient

    async def _sessions():
        client = DaemonClient(workspace_root=root_dir)
        if not await client.connect():
            # Fallback to local database
            from kitt.core.runtime import KittRuntime
            from kitt.core.runtime_config import RuntimeConfig
            rt = KittRuntime.build(root_dir, config=RuntimeConfig())
            convs = rt.history.list_history(limit=20)
            active = rt.history.get_active_read_only()
            active_id = active["id"] if active else ""
            print("\n\033[1;36m=== KITT Sessions (Local) ===\033[0m")
            for c in convs:
                tag = "\033[32m[ACTIVE]\033[0m" if c.get("id") == active_id else ""
                print(f"  • \033[1m{c.get('id', '')[:12]}\033[0m {tag} {c.get('title', '')}")
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
        client = DaemonClient(workspace_root=root_dir)
        if not await client.connect():
            print("\033[31mError: KITT Daemon is not running. Start it with 'kitt daemon start'\033[0m")
            return 1

        def on_event(evt):
            print(f"[{evt.event_type}] {evt.payload}")

        res = await client.attach(session_id, event_callback=on_event, workspace=root_dir)
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


def handle_detach_command(root_dir: str = ".") -> int:
    import asyncio
    from kitt.daemon.client import DaemonClient

    async def _detach():
        client = DaemonClient(workspace_root=root_dir)
        if await client.connect():
            await client.detach()
            await client.close()
            print("\033[32m✓ Detached from active session.\033[0m")
        else:
            print("\033[90mNo active daemon connection.\033[0m")
        return 0

    return asyncio.run(_detach())


def handle_resume_command(session_id: str, root_dir: str = ".") -> int:
    # Resuming validates session, restores execution context, and attaches
    return handle_attach_command(session_id, root_dir=root_dir)
