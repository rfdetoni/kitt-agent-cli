"""Minimal stdio MCP adapter for bounded, read-only binary inspection."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from binary_inspect import BinaryInspectionError, inspect_file

PROTOCOL = "2025-03-26"
TOOL = {
    "name": "binary.inspect",
    "description": "Inspect PE/ELF metadata from a regular file within the configured workspace; never executes it.",
    "inputSchema": {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Workspace-relative file path"}},
        "required": ["path"],
        "additionalProperties": False,
    },
}


def handle(message: dict, root: Path) -> dict | None:
    if not isinstance(message, dict):
        return None
    method, request_id = message.get("method"), message.get("id")
    if request_id is None:
        return None
    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "kitt-reverse-engineering-example", "version": "0.1.0"},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [TOOL]}
    elif method == "tools/call":
        params = message.get("params")
        if not isinstance(params, dict) or params.get("name") != TOOL["name"]:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "Unknown tool"}}
        args = params.get("arguments", {})
        if not isinstance(args, dict) or set(args) != {"path"}:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "Invalid tool arguments"}}
        try:
            value = inspect_file(root, args["path"])
            result = {"content": [{"type": "text", "text": json.dumps(value, separators=(",", ":"))}], "isError": False}
        except (BinaryInspectionError, OSError) as exc:
            result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    else:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}}
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True, type=Path)
    args = parser.parse_args()
    root = args.workspace.resolve(strict=True)
    if not root.is_dir():
        parser.error("--workspace must be a directory")
    for line in sys.stdin:
        try:
            payload = json.loads(line)
            reply = handle(payload, root)
        except (ValueError, TypeError, KeyError) as exc:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Invalid JSON-RPC request"}}
            print(f"invalid request: {type(exc).__name__}", file=sys.stderr)
        if reply is not None:
            sys.stdout.write(json.dumps(reply, separators=(",", ":")) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
