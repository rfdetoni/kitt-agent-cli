"""Figma integration using KITT's governed MCP boundary."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
from urllib.parse import parse_qs, quote, urlsplit

FIGMA_DESKTOP_MCP_URL = "http://127.0.0.1:3845/mcp"
FIGMA_REMOTE_MCP_URL = "https://mcp.figma.com/mcp"

_IGNORED_DIRS = {
    ".git",
    ".kitt",
    ".venv",
    "venv",
    "node_modules",
    "target",
    "build",
    "dist",
    ".gradle",
    ".next",
    "__pycache__",
}
_SCAN_SUFFIXES = {
    ".md",
    ".mdx",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".html",
    ".css",
    ".scss",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
}
_FIGMA_URL_RE = re.compile(
    r"https://(?:www\.)?figma\.com/[^\s<>\"')]+",
    re.IGNORECASE,
)
_FILE_TYPES = {"design", "file", "proto", "board", "make", "slides"}
_FILE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{4,256}$")


def _root(ctx) -> Path:
    root = getattr(ctx, "workspace_root", None)
    if root is None:
        raise RuntimeError("Figma plugin requires workspace_root")
    return Path(root).resolve()


def _normalize_node_id(value: str | None) -> str | None:
    node_id = str(value or "").strip()
    if not node_id:
        return None
    if ":" not in node_id and re.fullmatch(r"\d+(?:-\d+)+", node_id):
        return node_id.replace("-", ":")
    return node_id[:256]


def _parse_figma_url(value: str) -> dict | None:
    raw = str(value or "").strip().rstrip(".,;]")
    if not raw:
        return None
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if host not in {"figma.com", "www.figma.com"}:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or parts[0].lower() not in _FILE_TYPES:
        return None
    file_type = parts[0].lower()
    file_key = parts[1]
    if not _FILE_KEY_RE.fullmatch(file_key):
        return None
    query = parse_qs(parsed.query, keep_blank_values=False)
    raw_node_id = (query.get("node-id") or [None])[0]
    node_id = _normalize_node_id(raw_node_id)
    canonical = f"https://www.figma.com/{file_type}/{file_key}"
    if raw_node_id:
        canonical += "?node-id=" + quote(str(raw_node_id), safe=":-")
    return {
        "url": canonical,
        "file_type": file_type,
        "file_key": file_key,
        "node_id": node_id,
    }


def _workspace_links(root: Path, limit: int = 32) -> list[dict]:
    links: list[dict] = []
    seen: set[tuple[str, str | None]] = set()
    scanned = 0
    for current, dirs, names in os.walk(root):
        dirs[:] = [name for name in dirs if name not in _IGNORED_DIRS]
        for name in names:
            path = Path(current) / name
            if path.suffix.lower() not in _SCAN_SUFFIXES:
                continue
            try:
                resolved = path.resolve()
                resolved.relative_to(root)
                if path.is_symlink():
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")[:262144]
            except (OSError, ValueError):
                continue
            scanned += 1
            for match in _FIGMA_URL_RE.findall(text):
                target = _parse_figma_url(match)
                if target is None:
                    continue
                key = (target["file_key"], target["node_id"])
                if key in seen:
                    continue
                seen.add(key)
                links.append(
                    {
                        **target,
                        "source_path": resolved.relative_to(root).as_posix(),
                    }
                )
                if len(links) >= limit:
                    return links
            if scanned >= 1500:
                return links
    return links


def _figma_servers(ctx) -> list[dict]:
    rows = []
    for server in ctx.mcp.list_servers():
        server_id = str(server.get("server_id") or "").lower()
        url = str(server.get("url") or "")
        if "figma" in server_id or url in {
            FIGMA_DESKTOP_MCP_URL,
            FIGMA_REMOTE_MCP_URL,
        }:
            rows.append(server)
    return rows


def _preferred_server(servers: list[dict]) -> str | None:
    for server in servers:
        if server.get("url") == FIGMA_REMOTE_MCP_URL:
            return str(server.get("server_id") or "") or None
    for server in servers:
        if server.get("url") == FIGMA_DESKTOP_MCP_URL:
            return str(server.get("server_id") or "") or None
    return str(servers[0].get("server_id") or "") or None if servers else None


class _FigmaPluginHandle:
    def __init__(self, ctx):
        self.ctx = ctx

    async def start(self) -> None:
        registration = self.ctx.mcp.register_http(
            "figma-desktop",
            FIGMA_DESKTOP_MCP_URL,
            enabled=True,
            trust="restricted",
            timeout_seconds=15.0,
        )
        if registration.get("registered"):
            self.ctx.logger.info(
                "Registered official Figma desktop MCP adapter at %s",
                FIGMA_DESKTOP_MCP_URL,
            )

    async def stop(self) -> None:
        await self.ctx.mcp.unregister_owned()


def setup(ctx):
    root = _root(ctx)

    def inspect(args=None):
        payload = args if isinstance(args, dict) else {}
        target = _parse_figma_url(str(payload.get("url") or ""))
        servers = _figma_servers(ctx)
        preferred = _preferred_server(servers)
        result = {
            "target": target,
            "workspace_links": (
                _workspace_links(root)
                if bool(payload.get("scan_workspace", False))
                else []
            ),
            "mcp_servers": servers,
            "preferred_mcp_server": preferred,
            "mcp_tool_prefix": f"mcp.{preferred}." if preferred else None,
            "desktop_mcp_url": FIGMA_DESKTOP_MCP_URL,
            "remote_mcp_url": FIGMA_REMOTE_MCP_URL,
            "execution": (
                "Figma operations are executed only through KITT MCP tools; "
                "this plugin performs no direct network calls and reads no Figma tokens."
            ),
            "remote_note": (
                "Figma currently restricts its hosted MCP endpoint to supported/catalog "
                "clients. KITT registers the official desktop MCP endpoint by default; "
                "a separately authorized remote server can take precedence when configured."
            ),
        }
        return json.dumps(result, ensure_ascii=False, sort_keys=True)

    ctx.tools.register(
        "figma_inspect",
        inspect,
        description=(
            "Parse Figma design URLs, discover Figma references in the workspace, "
            "and report the governed official MCP bridge/tool namespace."
        ),
        schema={
            "url": "optional Figma design/file/board/make URL",
            "scan_workspace": "optional boolean; scan bounded text/source files for Figma links",
        },
    )
    return _FigmaPluginHandle(ctx)
