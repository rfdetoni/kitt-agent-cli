"""MCP Manager coordinating connections, tools and lifecycle."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from kitt.extensions.errors import MCPError
from kitt.extensions.mcp.client import MCPClient
from kitt.extensions.mcp.models import (
    MCPServerConfig,
    MCPServerState,
    MCPTool,
)
from kitt.extensions.mcp.security import MCPTrustStore
from kitt.extensions.mcp.tool_adapter import MCPToolAdapter
from kitt.extensions.mcp.transport import (
    HTTPTransport,
    MCPTransport,
    StdioTransport,
)

logger = logging.getLogger("kitt.extensions.mcp.manager")
_MAX_CONFIG_BYTES = 1024 * 1024


class MCPManager:
    def __init__(
        self,
        workspace_root: str = ".",
        config_file: Optional[str] = None,
        tool_registry=None,
        trust_store: Optional[MCPTrustStore] = None,
    ):
        self.workspace_root = Path(workspace_root).resolve()
        self.config_file = Path(
            config_file
            or (Path.home() / ".kitt" / "config" / "mcp.json")
        ).resolve()
        self.tool_registry = tool_registry
        self.trust_store = trust_store or MCPTrustStore(self.workspace_root)
        self._lock = threading.RLock()
        self._configs: Dict[str, MCPServerConfig] = {}
        self._clients: Dict[str, MCPClient] = {}
        self._custom_transports: Dict[str, MCPTransport] = {}
        self._server_tools: Dict[str, List[MCPTool]] = {}
        self._async_locks: Dict[str, asyncio.Lock] = {}
        self._load_configs()

    @staticmethod
    def _server_id(value: str) -> str:
        return str(value or "").strip().lower()

    @staticmethod
    def _transport_kind(value: str) -> str:
        return (
            str(value or "stdio")
            .strip()
            .lower()
            .replace("-", "_")
        )

    def _server_lock(self, server_id: str) -> asyncio.Lock:
        with self._lock:
            lock = self._async_locks.get(server_id)
            if lock is None:
                lock = asyncio.Lock()
                self._async_locks[server_id] = lock
            return lock

    def _load_configs(self) -> None:
        configs_data: Dict[str, Any] = {}
        workspace_server_ids: set[str] = set()

        def _read_config(path: Path) -> dict[str, Any]:
            if not path.exists():
                return {}
            if path.is_symlink():
                raise MCPError(f"Refusing symlink MCP config: {path}")

            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            try:
                fd = os.open(str(path), flags)
            except FileNotFoundError:
                return {}
            except OSError as exc:
                raise MCPError(
                    f"Unable to securely open MCP config {path}: {exc}"
                ) from exc

            try:
                stat_result = os.fstat(fd)
                if stat_result.st_size > _MAX_CONFIG_BYTES:
                    raise MCPError(
                        f"MCP config exceeds {_MAX_CONFIG_BYTES} bytes: {path}"
                    )
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = os.read(
                        fd,
                        min(
                            64 * 1024,
                            (_MAX_CONFIG_BYTES + 1) - total,
                        ),
                    )
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > _MAX_CONFIG_BYTES:
                        raise MCPError(
                            f"MCP config exceeds {_MAX_CONFIG_BYTES} bytes: {path}"
                        )
                    chunks.append(chunk)
            finally:
                os.close(fd)

            try:
                data = json.loads(
                    b"".join(chunks).decode("utf-8")
                )
            except Exception as exc:
                raise MCPError(
                    f"Invalid MCP config {path}: {exc}"
                ) from exc
            if not isinstance(data, dict):
                raise MCPError(
                    f"MCP config {path} must contain an object"
                )
            payload = data.get("mcp", data)
            if not isinstance(payload, dict):
                raise MCPError(
                    f"MCP config {path} field 'mcp' must be an object"
                )
            return payload

        try:
            configs_data.update(_read_config(self.config_file))
        except Exception as exc:
            logger.warning(
                "Failed to load global MCP config from %s: %s",
                self.config_file,
                exc,
            )

        workspace_file = self.workspace_root / ".kitt" / "mcp.json"
        try:
            workspace_data = _read_config(workspace_file)
            configs_data.update(workspace_data)
            workspace_server_ids = {
                self._server_id(server_id)
                for server_id in workspace_data
            }
        except Exception as exc:
            logger.warning(
                "Failed to load workspace MCP config from %s: %s",
                workspace_file,
                exc,
            )

        for raw_id, raw in configs_data.items():
            if not isinstance(raw, dict):
                continue
            server_id = self._server_id(raw_id)
            if not server_id:
                continue
            self._configs[server_id] = MCPServerConfig(
                server_id=server_id,
                transport=self._transport_kind(
                    raw.get("transport", "stdio")
                ),
                command=(
                    str(raw["command"])
                    if raw.get("command") is not None
                    else None
                ),
                args=[
                    str(value)
                    for value in (
                        raw.get("args", [])
                        if isinstance(raw.get("args", []), list)
                        else []
                    )
                ],
                env={
                    str(k): str(v)
                    for k, v in (
                        raw.get("env", {})
                        if isinstance(raw.get("env", {}), dict)
                        else {}
                    ).items()
                },
                url=raw.get("url"),
                headers={
                    str(k): str(v)
                    for k, v in (
                        raw.get("headers", {})
                        if isinstance(raw.get("headers", {}), dict)
                        else {}
                    ).items()
                },
                enabled=(
                    raw.get("enabled", True)
                    if isinstance(raw.get("enabled", True), bool)
                    else True
                ),
                trust=str(raw.get("trust", "restricted")),
                allow_tools=raw.get("allow_tools"),
                deny_tools=list(raw.get("deny_tools", [])),
                timeout_seconds=float(
                    raw.get("timeout_seconds", 30.0)
                ),
                max_output_bytes=int(
                    raw.get("max_output_bytes", 2 * 1024 * 1024)
                ),
                source=(
                    "workspace"
                    if server_id in workspace_server_ids
                    else "global"
                ),
            )

    def get_config(self, server_id: str) -> MCPServerConfig:
        server_id = self._server_id(server_id)
        with self._lock:
            config = self._configs.get(server_id)
        if config is None:
            raise MCPError(f"MCP server '{server_id}' not found")
        return config

    def is_trusted(self, server_id: str) -> bool:
        return self.trust_store.is_trusted(
            self.get_config(server_id)
        )

    def trust_server(self, server_id: str) -> str:
        return self.trust_store.grant(self.get_config(server_id))

    async def untrust_server(self, server_id: str) -> bool:
        server_id = self._server_id(server_id)
        await self.disconnect(server_id)
        return self.trust_store.revoke(server_id)

    def register_server(
        self,
        config: MCPServerConfig,
        custom_transport: Optional[MCPTransport] = None,
    ) -> None:
        server_id = self._server_id(config.server_id)
        config.server_id = server_id
        config.transport = self._transport_kind(config.transport)
        with self._lock:
            self._configs[server_id] = config
            if custom_transport is not None:
                self._custom_transports[server_id] = custom_transport

    def _build_transport(
        self,
        config: MCPServerConfig,
        override: Optional[MCPTransport] = None,
    ) -> MCPTransport:
        if override is not None:
            return override
        with self._lock:
            custom = self._custom_transports.get(config.server_id)
        if custom is not None:
            return custom

        kind = self._transport_kind(config.transport)
        if kind == "stdio":
            if not config.command:
                raise MCPError(
                    f"MCP stdio server '{config.server_id}' requires command"
                )
            return StdioTransport(
                command=config.command,
                args=config.args,
                env=config.env,
                cwd=str(self.workspace_root),
            )
        if kind in {"http", "streamable_http"}:
            if not config.url:
                raise MCPError(
                    f"MCP HTTP server '{config.server_id}' requires url"
                )
            return HTTPTransport(
                url=config.url,
                timeout_seconds=config.timeout_seconds,
                headers=config.headers,
            )
        if kind == "inprocess":
            raise MCPError(
                f"MCP inprocess server '{config.server_id}' requires "
                "a trusted custom transport"
            )
        raise MCPError(
            f"Unsupported MCP transport '{config.transport}'"
        )

    async def connect(
        self,
        server_id: str,
        transport: Optional[MCPTransport] = None,
    ) -> MCPClient:
        server_id = self._server_id(server_id)
        if not server_id:
            raise MCPError("MCP server id is required")

        async with self._server_lock(server_id):
            with self._lock:
                config = self._configs.get(server_id)
                client = self._clients.get(server_id)
            if config is None:
                raise MCPError(
                    f"MCP server '{server_id}' not found"
                )
            self.trust_store.assert_trusted(config)
            current_loop = asyncio.get_running_loop()
            if client is not None and client.owner_loop is not None:
                if client.owner_loop is not current_loop:
                    raise MCPError(
                        f"MCP server '{server_id}' is owned by another "
                        "event loop"
                    )
            if (
                client is not None
                and client.state == MCPServerState.CONNECTED
            ):
                return client

            if client is not None:
                try:
                    await client.disconnect()
                except Exception:
                    logger.debug(
                        "Discarding stale MCP client",
                        exc_info=True,
                    )

            client = MCPClient(
                config,
                self._build_transport(config, transport),
            )
            with self._lock:
                self._clients[server_id] = client

            try:
                await client.connect()
                capabilities = getattr(
                    client, "_server_capabilities", {}
                )
                tools = (
                    await client.list_tools()
                    if "tools" in capabilities
                    else []
                )
            except Exception:
                with self._lock:
                    self._clients.pop(server_id, None)
                    self._server_tools.pop(server_id, None)
                try:
                    await client.disconnect()
                except Exception:
                    pass
                raise

            with self._lock:
                self._server_tools[server_id] = tools

            if (
                self.tool_registry
                and hasattr(
                    self.tool_registry, "unregister_by_owner"
                )
            ):
                self.tool_registry.unregister_by_owner(
                    f"mcp:{server_id}"
                )
            if (
                self.tool_registry
                and hasattr(self.tool_registry, "register")
            ):
                for tool in tools:
                    adapter = MCPToolAdapter(tool, client)
                    self.tool_registry.register(
                        tool.full_name,
                        adapter.execute,
                        description=adapter.description,
                        schema=adapter.schema,
                        owner_plugin_id=f"mcp:{server_id}",
                    )
            return client

    async def disconnect(self, server_id: str) -> None:
        server_id = self._server_id(server_id)
        if not server_id:
            return
        async with self._server_lock(server_id):
            with self._lock:
                client = self._clients.get(server_id)
            if (
                client is not None
                and client.owner_loop is not None
                and client.owner_loop is not asyncio.get_running_loop()
            ):
                raise MCPError(
                    f"MCP server '{server_id}' is owned by another event loop"
                )
            with self._lock:
                client = self._clients.pop(server_id, None)
                self._server_tools.pop(server_id, None)
            if (
                self.tool_registry
                and hasattr(
                    self.tool_registry, "unregister_by_owner"
                )
            ):
                self.tool_registry.unregister_by_owner(
                    f"mcp:{server_id}"
                )
            if client is not None:
                await client.disconnect()

    async def reconnect(self, server_id: str) -> MCPClient:
        await self.disconnect(server_id)
        return await self.connect(server_id)

    def list_servers(self) -> List[MCPServerConfig]:
        with self._lock:
            return list(self._configs.values())

    def get_server_status(
        self, server_id: str
    ) -> MCPServerState:
        with self._lock:
            client = self._clients.get(
                self._server_id(server_id)
            )
            return (
                client.state
                if client
                else MCPServerState.DISCONNECTED
            )

    def list_tools(
        self,
        server_id: Optional[str] = None,
    ) -> List[MCPTool]:
        with self._lock:
            if server_id:
                return list(
                    self._server_tools.get(
                        self._server_id(server_id), []
                    )
                )
            result: List[MCPTool] = []
            for tools in self._server_tools.values():
                result.extend(tools)
            return result

    async def connect_all_enabled(self) -> None:
        with self._lock:
            configs = list(self._configs.values())
        for config in configs:
            if not config.enabled:
                continue
            if not self.trust_store.is_trusted(config):
                logger.warning(
                    "Skipping untrusted workspace MCP server '%s'; "
                    "run 'kitt mcp trust %s' after review",
                    config.server_id,
                    config.server_id,
                )
                continue
            kind = self._transport_kind(config.transport)
            with self._lock:
                custom = config.server_id in self._custom_transports
            ready = (
                bool(config.command)
                if kind == "stdio"
                else bool(config.url)
                if kind in {"http", "streamable_http"}
                else custom
                if kind == "inprocess"
                else False
            )
            if not ready:
                logger.warning(
                    "Enabled MCP server '%s' has invalid %s configuration",
                    config.server_id,
                    config.transport,
                )
                continue
            try:
                await self.connect(config.server_id)
            except Exception as exc:
                logger.warning(
                    "Failed to connect enabled MCP server '%s': %s",
                    config.server_id,
                    exc,
                )

    async def disconnect_all(self) -> None:
        with self._lock:
            server_ids = list(self._clients)
        errors: list[str] = []
        for server_id in server_ids:
            try:
                await self.disconnect(server_id)
            except Exception as exc:
                errors.append(f"{server_id}: {exc}")
        if errors:
            raise RuntimeError(
                "MCP shutdown errors: " + "; ".join(errors)
            )
