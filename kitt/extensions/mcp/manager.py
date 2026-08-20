"""MCP Manager coordinating connections, tools and lifecycle."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import stat
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
            os.path.abspath(
                os.path.expanduser(
                    str(
                        config_file
                        or (
                            Path.home()
                            / ".kitt"
                            / "config"
                            / "mcp.json"
                        )
                    )
                )
            )
        )
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
        def _read_config(
            path: Path,
            *,
            trusted_source: bool = False,
        ) -> dict[str, Any]:
            if not path.exists():
                return {}
            if path.is_symlink():
                raise MCPError(f"Refusing symlink MCP config: {path}")

            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            if hasattr(os, "O_NONBLOCK"):
                flags |= os.O_NONBLOCK
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
                if not stat.S_ISREG(stat_result.st_mode):
                    raise MCPError(
                        f"MCP config must be a regular file: {path}"
                    )
                if trusted_source and os.name != "nt":
                    if stat_result.st_uid != os.getuid():
                        raise MCPError(
                            f"Trusted MCP config owner mismatch: {path}"
                        )
                    if stat.S_IMODE(stat_result.st_mode) & 0o077:
                        raise MCPError(
                            "Trusted MCP config must not be accessible "
                            f"by group/other; run chmod 600 {path}"
                        )
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

        def _string_list(
            value: Any,
            field: str,
            *,
            optional: bool = False,
            max_items: int = 512,
            max_item_chars: int = 8192,
        ) -> Optional[List[str]]:
            if value is None and optional:
                return None
            if not isinstance(value, list):
                raise MCPError(
                    f"MCP field '{field}' must be a JSON array"
                )
            if len(value) > max_items:
                raise MCPError(
                    f"MCP field '{field}' exceeds {max_items} items"
                )
            result: List[str] = []
            for item in value:
                if not isinstance(item, str):
                    raise MCPError(
                        f"MCP field '{field}' must contain strings only"
                    )
                if len(item) > max_item_chars:
                    raise MCPError(
                        f"MCP field '{field}' contains an oversized value"
                    )
                result.append(item)
            return result

        def _string_map(
            value: Any,
            field: str,
            *,
            max_items: int = 256,
            max_key_chars: int = 512,
            max_value_chars: int = 8192,
        ) -> Dict[str, str]:
            if not isinstance(value, dict):
                raise MCPError(
                    f"MCP field '{field}' must be a JSON object"
                )
            if len(value) > max_items:
                raise MCPError(
                    f"MCP field '{field}' exceeds {max_items} entries"
                )
            result: Dict[str, str] = {}
            for key, item in value.items():
                if not isinstance(key, str) or not isinstance(item, str):
                    raise MCPError(
                        f"MCP field '{field}' keys/values must be strings"
                    )
                if len(key) > max_key_chars or len(item) > max_value_chars:
                    raise MCPError(
                        f"MCP field '{field}' contains an oversized entry"
                    )
                result[key] = item
            return result

        def _bounded_float(
            value: Any,
            field: str,
            *,
            minimum: float,
            maximum: float,
        ) -> float:
            if isinstance(value, bool) or not isinstance(
                value, (int, float)
            ):
                raise MCPError(
                    f"MCP field '{field}' must be numeric"
                )
            parsed = float(value)
            if not math.isfinite(parsed):
                raise MCPError(
                    f"MCP field '{field}' must be finite"
                )
            if parsed < minimum or parsed > maximum:
                raise MCPError(
                    f"MCP field '{field}' must be between {minimum} and {maximum}"
                )
            return parsed

        def _bounded_int(
            value: Any,
            field: str,
            *,
            minimum: int,
            maximum: int,
        ) -> int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise MCPError(
                    f"MCP field '{field}' must be an integer"
                )
            if value < minimum or value > maximum:
                raise MCPError(
                    f"MCP field '{field}' must be between {minimum} and {maximum}"
                )
            return value

        def _optional_string(
            value: Any,
            field: str,
            *,
            max_chars: int = 16384,
        ) -> Optional[str]:
            if value is None:
                return None
            if not isinstance(value, str):
                raise MCPError(
                    f"MCP field '{field}' must be a string or null"
                )
            if len(value) > max_chars:
                raise MCPError(
                    f"MCP field '{field}' is oversized"
                )
            return value

        def _parse_server(
            raw_id: Any,
            raw: Any,
            *,
            source: str,
        ) -> MCPServerConfig:
            server_id = self._server_id(raw_id)
            if not server_id:
                raise MCPError("MCP server id is empty")
            if len(server_id) > 128:
                raise MCPError(
                    "MCP server id exceeds 128 characters"
                )
            if not isinstance(raw, dict):
                raise MCPError(
                    f"MCP server '{server_id}' config must be an object"
                )

            raw_transport = raw.get("transport", "stdio")
            if not isinstance(raw_transport, str):
                raise MCPError(
                    f"MCP server '{server_id}' field 'transport' must be string"
                )
            transport = self._transport_kind(raw_transport)
            if transport not in {
                "stdio",
                "http",
                "streamable_http",
                "inprocess",
            }:
                raise MCPError(
                    f"MCP server '{server_id}' has unsupported transport '{transport}'"
                )

            enabled = raw.get("enabled", True)
            if not isinstance(enabled, bool):
                raise MCPError(
                    f"MCP server '{server_id}' field 'enabled' must be boolean"
                )

            trust = raw.get("trust", "restricted")
            if not isinstance(trust, str):
                raise MCPError(
                    f"MCP server '{server_id}' field 'trust' must be string"
                )
            trust = trust.strip().lower()
            if trust not in {"trusted", "restricted", "isolated"}:
                raise MCPError(
                    f"MCP server '{server_id}' has invalid trust mode"
                )

            return MCPServerConfig(
                server_id=server_id,
                transport=transport,
                command=_optional_string(raw.get("command"), "command"),
                args=_string_list(
                    raw.get("args", []),
                    "args",
                    max_items=256,
                )
                or [],
                env=_string_map(raw.get("env", {}), "env"),
                url=_optional_string(raw.get("url"), "url"),
                headers=_string_map(raw.get("headers", {}), "headers"),
                enabled=enabled,
                trust=trust,
                allow_tools=_string_list(
                    raw.get("allow_tools"),
                    "allow_tools",
                    optional=True,
                ),
                deny_tools=_string_list(
                    raw.get("deny_tools", []),
                    "deny_tools",
                )
                or [],
                timeout_seconds=_bounded_float(
                    raw.get("timeout_seconds", 30.0),
                    "timeout_seconds",
                    minimum=0.1,
                    maximum=300.0,
                ),
                max_output_bytes=_bounded_int(
                    raw.get("max_output_bytes", 2 * 1024 * 1024),
                    "max_output_bytes",
                    minimum=1024,
                    maximum=64 * 1024 * 1024,
                ),
                source=source,
            )

        configs: Dict[str, MCPServerConfig] = {}

        try:
            global_data = _read_config(
                self.config_file,
                trusted_source=True,
            )
        except Exception as exc:
            logger.warning(
                "Failed to load global MCP config from %s: %s",
                self.config_file,
                exc,
            )
            global_data = {}

        for raw_id, raw in global_data.items():
            try:
                config = _parse_server(
                    raw_id,
                    raw,
                    source="global",
                )
            except Exception as exc:
                logger.warning(
                    "Skipping invalid global MCP server '%s': %s",
                    raw_id,
                    exc,
                )
                continue
            configs[config.server_id] = config

        workspace_file = self.workspace_root / ".kitt" / "mcp.json"
        try:
            workspace_data = _read_config(workspace_file)
        except Exception as exc:
            logger.warning(
                "Failed to load workspace MCP config from %s: %s",
                workspace_file,
                exc,
            )
            workspace_data = {}

        for raw_id, raw in workspace_data.items():
            try:
                config = _parse_server(
                    raw_id,
                    raw,
                    source="workspace",
                )
            except Exception as exc:
                logger.warning(
                    "Skipping invalid workspace MCP server '%s': %s",
                    raw_id,
                    exc,
                )
                continue
            if config.server_id in configs:
                logger.warning(
                    "Ignoring workspace MCP server '%s': id collides with a global MCP server",
                    config.server_id,
                )
                continue
            configs[config.server_id] = config

        with self._lock:
            self._configs = configs

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
