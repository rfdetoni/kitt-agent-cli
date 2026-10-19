"""Operator-only read-only RE guest MCP bridge (stdio, no guest shell).

Runs on the KITT host and delegates to the existing managed Docker inventory
and operator-pinned forced-command SSH Windows Oracle readiness check.

Do not enable in the implementation agent's isolated Clean-Room container.
It intentionally does not expose guest filesystem access, arbitrary process
execution, source code, probe outputs or guest-to-guest communication.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PROTOCOL = "2024-11-05"
MAX_REQUEST = 16384
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
TOOLS = [
    {
        "name": "guests_status",
        "description": "Read-only status of operator-managed Linux and Windows RE guests; no execution.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "windows_oracle_doctor",
        "description": "Check existing Windows Oracle forced-command SSH readiness and pinned manifest; never run probes.",
        "inputSchema": {
            "type": "object", "properties": {
                "manifest_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"}
            }, "required": ["manifest_sha256"], "additionalProperties": False,
        },
    },
]


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": code, "message": message}}


def respond(request: dict, *, state_root: Path | None = None) -> dict | None:
    """Return a bounded, nonprivileged MCP response; tool errors fail closed."""
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
        return _error(None, -32600, "Invalid Request")
    if "id" not in request:
        return None
    request_id = request["id"]
    if isinstance(request_id, bool) or not isinstance(request_id, (int, str)):
        return _error(None, -32600, "Invalid Request ID")
    method = request.get("method")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": request_id,
                "result": {"protocolVersion": PROTOCOL,
                           "capabilities": {"tools": {"listChanged": False}},
                           "serverInfo": {"name": "kitt-re-guests", "version": "0.1.0"}}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method != "tools/call":
        return _error(request_id, -32601, "Method not found")
    params = request.get("params")
    if not isinstance(params, dict) or set(params) != {"name", "arguments"}:
        return _error(request_id, -32602, "Invalid parameters")
    name, args = params["name"], params["arguments"]
    if not isinstance(args, dict):
        return _error(request_id, -32602, "Invalid tool arguments")
    try:
        if name == "guests_status" and not args:
            from kitt.re_lab import status
            observation = status(state_root=state_root)
            if not isinstance(observation, dict):
                raise ValueError("Unrecognized guest status")
            # Only status metadata may cross the MCP boundary: no host paths,
            # container IDs, SSH configuration or analyst artefacts.
            guests = observation.get("guests")
            cleaned = {}
            if isinstance(guests, dict):
                for role in ("linux", "windows"):
                    entry = guests.get(role)
                    if isinstance(entry, dict):
                        cleaned[role] = {
                            k: entry[k] for k in ("status", "backend", "environment", "managed")
                            if type(entry.get(k)) in (str, bool)
                        }
            result_data = {"status": observation.get("status", "BLOCKED_ENV"),
                           "guests": cleaned, "oracle_ready": False,
                           "guest_attested": False}
        elif name == "windows_oracle_doctor" and set(args) == {"manifest_sha256"}:
            digest = args["manifest_sha256"]
            if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
                return _error(request_id, -32602, "Invalid manifest digest")
            from kitt.re_oracle import doctor
            observation = doctor(manifest_sha256=digest)
            if not isinstance(observation, dict):
                raise ValueError("Unrecognized oracle result")
            result_data = {
                "status": observation.get("status", "BLOCKED_ENV"),
                "oracle_ready": observation.get("oracle_ready") is True,
                "guest_attested": False,
            }
            if "reason" in observation:
                result_data["reason"] = str(observation["reason"])[:200]
            if isinstance(observation.get("guest"), dict):
                guest = observation["guest"]
                result_data["guest"] = {
                    k: guest[k] for k in ("platform", "architecture", "manifest_sha256")
                    if isinstance(guest.get(k), str)
                }
        else:
            return _error(request_id, -32602, "Unknown or forbidden tool")
    except (OSError, ValueError, RuntimeError) as exc:
        result_data = {"status": "BLOCKED_ENV", "reason": type(exc).__name__}
    text = json.dumps(result_data, ensure_ascii=True, separators=(",", ":"))
    if len(text.encode("utf-8")) > 8192:
        text = '{"status":"BLOCKED_ENV","reason":"Response too large"}'
    return {"jsonrpc": "2.0", "id": request_id,
            "result": {"content": [{"type": "text", "text": text}], "isError": False}}


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only host-owned RE guest MCP server")
    parser.add_argument("--role", choices=["operator"], required=True)
    parser.add_argument("--state-root", type=Path, default=None)
    args = parser.parse_args()
    # The MCP host config grants access; model tool arguments never select role.
    if args.role != "operator":
        parser.error("Operator-only guest MCP transport")
    for raw in sys.stdin.buffer:
        if len(raw) > MAX_REQUEST:
            reply = _error(None, -32700, "Request too large")
        else:
            try:
                data = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=_unique_keys,
                                  parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite")))
                reply = respond(data, state_root=args.state_root)
            except (ValueError, UnicodeError, TypeError):
                reply = _error(None, -32700, "Invalid JSON frame")
        if reply is not None:
            print(json.dumps(reply, ensure_ascii=True, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
