"""Opt-in KITT first-party stdio MCP adapter: no process execution."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .binary import InspectionError, inspect
from .evidence import EvidenceError, compare
from .probe_evidence import compare_probe_receipts

VERSION = "2024-11-05"
def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


TOOLS = [
    {"name": "binary_inspect", "description": "Read-only PE/ELF headers and SHA256, maximum 64 MiB, workspace relative.",
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}},
                     "required": ["path"], "additionalProperties": False}},
    {"name": "evidence_compare", "description": "Compare two JSON-compatible experiment observations deterministically.",
     "inputSchema": {"type": "object", "properties": {"reference": {}, "candidate": {}},
                     "required": ["reference", "candidate"], "additionalProperties": False}},
    {"name": "evidence_probe_compare", "description": "Verify two hash-linked VM probe receipts and compare their JSON observations.",
     "inputSchema": {"type": "object", "properties": {"reference": {"type": "object"}, "candidate": {"type": "object"}},
                     "required": ["reference", "candidate"], "additionalProperties": False}},
]


def respond(request: dict, workspace: Path) -> dict | None:
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
    if "id" not in request:
        return None
    request_id = request["id"]
    if isinstance(request_id, bool) or not isinstance(request_id, (int, str)):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request ID"}}
    method = request.get("method")
    try:
        if method == "initialize":
            result = {"protocolVersion": VERSION, "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": "kitt-reverse-engineering", "version": "0.1.0"}}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            params = request.get("params")
            if not isinstance(params, dict) or not isinstance(params.get("arguments"), dict):
                raise EvidenceError("Invalid arguments")
            name, values = params.get("name"), params["arguments"]
            if name == "binary_inspect" and set(values) == {"path"}:
                result_data = inspect(workspace, values["path"])
            elif name == "evidence_compare" and set(values) == {"reference", "candidate"}:
                result_data = compare(values["reference"], values["candidate"])
            elif name == "evidence_probe_compare" and set(values) == {"reference", "candidate"}:
                result_data = compare_probe_receipts(values["reference"], values["candidate"])
            else:
                raise EvidenceError("Unknown tool or invalid arguments")
            result = {"content": [{"type": "text", "text": json.dumps(result_data, ensure_ascii=False)}],
                      "isError": False}
        else:
            return {"jsonrpc": "2.0", "id": request_id,
                    "error": {"code": -32601, "message": "Method not found"}}
    except (InspectionError, EvidenceError) as error:
        result = {"content": [{"type": "text", "text": str(error)}], "isError": True}
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True, type=Path)
    args = parser.parse_args()
    root = args.workspace.resolve(strict=True)
    if not root.is_dir():
        parser.error("Workspace must exist and be a directory")
    for line in sys.stdin:
        if len(line) > 2 * 1024 * 1024:
            continue
        try:
            msg = json.loads(line, object_pairs_hook=_unique_pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite number")))
        except (ValueError, TypeError):
            reply = {"jsonrpc": "2.0", "id": None,
                     "error": {"code": -32700, "message": "Parse error"}}
        else:
            reply = respond(msg, root)
        if reply is not None:
            print(json.dumps(reply, ensure_ascii=False, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
