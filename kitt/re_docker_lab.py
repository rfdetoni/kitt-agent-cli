"""Recognize the existing installer-managed Docker RE lab without creating guests."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys

from kitt.vm_supervisor import VMError

_ROLES = {"linux": ("kitt-re-full", "toolbox", "workspace", "/workspace"),
          "windows": ("kitt-re-win11", "windows", "windows", "/storage")}
_SHA = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _docker(args: list[str], timeout: int = 15) -> subprocess.CompletedProcess:
    if sys.platform != "linux" or not shutil.which("docker"):
        raise VMError("Managed Docker RE lab requires a working Docker Engine on Linux")
    try:
        return subprocess.run([shutil.which("docker"), *args], capture_output=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VMError("Docker RE lab request failed or timed out") from exc


def _lab_root(value: object) -> Path:
    if not isinstance(value, str) or not value.startswith("/"):
        raise VMError("Missing absolute KITT RE lab root label")
    root = Path(value)
    if root.name != "re-lab" or root.is_symlink() or not root.is_dir():
        raise VMError("Invalid KITT RE lab root")
    if root.resolve(strict=True) != root:
        raise VMError("KITT RE lab root has a symbolic-link parent")
    info = root.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise VMError("KITT RE lab directory must be user-owned and private")
    return root


def _checked(record: dict, system: str) -> dict:
    name, role, source, destination = _ROLES[system]
    labels = (record.get("Config") or {}).get("Labels") or {}
    if (not isinstance(labels, dict) or record.get("Name") != "/" + name
            or labels.get("dev.kitt.re-lab") != "v1"
            or labels.get("dev.kitt.re-role") != role):
        raise VMError("Container is not owned by the KITT RE lab")
    lab = _lab_root(labels.get("dev.kitt.re-root"))
    matches = [m for m in record.get("Mounts", []) if isinstance(m, dict)
               and m.get("Destination") == destination]
    if (len(matches) != 1 or matches[0].get("Type") != "bind"
            or matches[0].get("Source") != str(lab / source)
            or not _SHA.fullmatch(str(record.get("Image", "")))):
        raise VMError("Docker RE container mount or image identity is not trusted")
    state = (record.get("State") or {}).get("Status")
    if state not in {"running", "created", "exited", "paused", "restarting", "dead"}:
        raise VMError("Unknown Docker RE container state")
    host = record.get("HostConfig") or {}
    if system == "linux":
        if (host.get("NetworkMode") != "none" or host.get("Privileged")
                or "ALL" not in (host.get("CapDrop") or [])):
            raise VMError("Linux RE toolbox does not match its isolation contract")
    else:
        bindings = (host.get("PortBindings") or {}).get("8006/tcp") or []
        if not any(isinstance(p, dict) and p.get("HostIp") == "127.0.0.1"
                   and p.get("HostPort") == "8006" for p in bindings):
            raise VMError("Dockur console is not restricted to localhost:8006")
    container_id = record.get("Id")
    if not isinstance(container_id, str) or not re.fullmatch(r"[a-f0-9]{64}", container_id):
        raise VMError("Invalid Docker RE container ID")
    return {
        "status": state, "backend": "docker",
        "environment": "linux-toolbox" if system == "linux" else "dockurr-windows",
        "container": name, "container_id": container_id, "managed": True,
        "auto": False, "oracle_ready": False,
        **({"console": "http://127.0.0.1:8006"} if system == "windows"
           else {"workspace": str(lab / "workspace"),
                 "tools": ["rea", "frida", "ghidra"]}),
    }


def inspect(system: str) -> dict | None:
    """Read-only: absent is None; mismatched labels/volumes are an error."""
    if system not in _ROLES:
        raise VMError("Unknown Docker RE environment")
    if sys.platform != "linux" or not shutil.which("docker"):
        return None
    response = _docker(["container", "inspect", _ROLES[system][0]])
    if response.returncode:
        if b"No such object" in response.stderr or b"No such container" in response.stderr:
            return None
        raise VMError("Docker RE inspection failed; check docker info")
    if len(response.stdout) > 256 * 1024:
        raise VMError("Docker RE inspection response too large")
    try:
        value = json.loads(response.stdout)
    except (ValueError, UnicodeError) as exc:
        raise VMError("Invalid Docker RE inspection JSON") from exc
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise VMError("Invalid Docker RE inspection shape")
    return _checked(value[0], system)


def operate(system: str, action: str) -> dict:
    """Explicit operator action on a validated container ID, never by mutable name."""
    if action not in {"start", "stop"}:
        raise VMError("Unsupported Docker RE operation")
    previous = inspect(system)
    if previous is None:
        raise VMError("KITT RE Docker container not provisioned")
    if previous["status"] in {"paused", "restarting", "dead"}:
        raise VMError("Docker RE container needs operator intervention")
    if (action == "start" and previous["status"] == "running") or (
        action == "stop" and previous["status"] != "running"
    ):
        return {**previous, "changed": False}
    result = _docker([action, previous["container_id"]], timeout=140 if action == "stop" else 30)
    if result.returncode:
        raise VMError("Cannot " + action + " KITT RE Docker container")
    current = inspect(system)
    if current is None or current["container_id"] != previous["container_id"]:
        raise VMError("KITT RE Docker container was replaced during operation")
    return {**current, "changed": True}


def shell(system: str) -> int:
    """Human TTY only; do not offer arbitrary Docker commands to the model."""
    if system != "linux":
        raise VMError("Only the Linux RE toolbox has an operator shell")
    current = inspect(system)
    if current is None or current["status"] != "running":
        raise VMError("The verified Linux RE toolbox is not running")
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise VMError("RE shell requires an interactive host terminal")
    return subprocess.run([shutil.which("docker"), "exec", "-it",
                           current["container_id"], "/bin/sh"],
                          check=False).returncode
