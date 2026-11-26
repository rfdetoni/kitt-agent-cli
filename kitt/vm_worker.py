"""Forced-command SSH VM worker: execute an autonomous KITT contract in guest.

Operator provisions the guest SSH authorized_keys command. No shell text from
the model is evaluated; goal is passed as one process argv element.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

MAX_REQUEST = 16 * 1024
MAX_OUTPUT = 256 * 1024


def handle(request: dict, workspace: Path, *, runner=None) -> dict:
    if not isinstance(request, dict) or type(request.get("version")) is not int or request["version"] != 1:
        raise ValueError("Invalid VM worker version")
    if os.environ.get("SSH_ORIGINAL_COMMAND"):
        raise ValueError("Guest SSH must use an operator-owned forced command")
    if os.name == "posix" and os.geteuid() == 0:
        raise ValueError("Refusing root VM worker")
    workspace = workspace.resolve(strict=True)
    if not workspace.is_dir():
        raise ValueError("Workspace missing")
    if request.get("method") == "vm.health" and set(request) == {"version", "method"}:
        from kitt import __version__
        marker = Path("/var/lib/kitt-bootstrap-ok")
        if not marker.is_file() or marker.stat().st_size != 41:
            raise ValueError("Guest bootstrap not complete")
        sha = marker.read_text("ascii").strip()
        if len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
            raise ValueError("Invalid guest bootstrap revision")
        return {"version": 1, "status": "READY",
                "python_minor": f"{sys.version_info.major}.{sys.version_info.minor}",
                "agent_version": __version__, "agent_sha": sha}
    if set(request) != {"version", "method", "goal"} or request["method"] != "agent.run":
        raise ValueError("Unknown VM worker request")
    goal = request["goal"]
    if not isinstance(goal, str) or not goal.strip() or len(goal.encode("utf-8")) > 8192:
        raise ValueError("Invalid goal")
    args = [
        sys.executable, "-m", "kitt.cli.main",
        "--root", str(workspace), "--ui", "plain",
        "--contract", "--contract-yes", "--allow", "all", "-p", goal,
    ]
    call = runner or subprocess.run
    child_env = dict(os.environ)
    child_env["KITT_DAEMON"] = "0"
    child_env["KITT_DAEMON_AUTO_START"] = "0"
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        result = call(
            args, cwd=workspace, stdin=subprocess.DEVNULL,
            stdout=out, stderr=err, check=False, timeout=3500, env=child_env,
        )
        out.seek(0, os.SEEK_END)
        length = out.tell()
        out.seek(0)
        output = out.read(MAX_OUTPUT)
    # A contract SUCCEEDED state returns zero; stdout claims never override exit.
    status = "PASS" if result.returncode == 0 and length <= MAX_OUTPUT else "BLOCKED"
    digest = hashlib.sha256(output).hexdigest()
    return {
        "version": 1, "status": status, "exit_code": result.returncode,
        "output_sha256": digest, "output": output.decode("utf-8", "replace"),
        "truncated": length > MAX_OUTPUT,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True, type=Path)
    args = parser.parse_args()
    if os.environ.get("SSH_ORIGINAL_COMMAND"):
        raise SystemExit(2)
    raw = sys.stdin.buffer.readline(MAX_REQUEST + 1)
    if not raw or len(raw) > MAX_REQUEST or not raw.endswith(b"\n"):
        raise SystemExit(2)
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate worker request key")
            result[key] = value
        return result
    try:
        request = json.loads(raw, object_pairs_hook=unique_pairs,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite")))
        result = handle(request, args.workspace)
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        result = {"version": 1, "status": "BLOCKED", "error": type(error).__name__}
    sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
