"""Operator-confirmed, scoped reset of verified, private KITT RE lab resources."""
from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import stat
import time

from kitt import re_docker_lab, re_lab
from kitt.vm_supervisor import VMError

_SCOPES = {"windows": ("windows",), "linux": ("linux",),
           "wine": (), "all": ("windows", "linux")}
_WINDOW = 600


def _inventory(root: Path, scope: str) -> dict:
    if scope not in _SCOPES:
        raise VMError("Unknown RE lab reset scope")
    re_docker_lab._lab_root(str(root))
    containers = {}
    for role in _SCOPES[scope]:
        item = re_docker_lab.inspect(role)
        if item is None:
            raise VMError("Cannot reset absent managed RE lab container: " + role)
        if role == "linux" and Path(item["workspace"]).parent != root:
            raise VMError("Linux RE toolbox belongs to a different lab")
        if role == "windows":
            # The Windows mount is checked by re_docker_lab.inspect.
            linux = re_docker_lab.inspect("linux")
            if linux is None or Path(linux["workspace"]).parent != root:
                raise VMError("Windows guest has no associated managed lab root")
        containers[role] = item["container_id"]
    targets = (["windows", "oracle", "windows.env"] if scope in ("windows", "all") else [])
    if scope in ("linux", "all"):
        targets.append("workspace")
    for name in targets:
        path = root / name
        if path.is_symlink() or (path.exists() and path.is_mount()):
            raise VMError("Unsafe RE lab reset target")
        if path.exists():
            for parent, dirs, files in os.walk(path, followlinks=False):
                for entry in dirs + files:
                    candidate = Path(parent) / entry
                    if candidate.is_symlink() or candidate.is_mount():
                        raise VMError("Unexpected link or nested mount in reset scope")
    return {"scope": scope, "root": str(root),
            "containers": containers, "deletes": [str(root / name) for name in targets]}


def _secret(root: Path) -> bytes:
    key = root / "oracle" / "client_ed25519"
    if (key.is_symlink() or not key.is_file() or
            key.stat().st_uid != os.getuid() or key.stat().st_size > 4096 or
            stat.S_IMODE(key.stat().st_mode) & 0o077):
        raise VMError("Missing operator-owned client key for scoped reset approval")
    return hashlib.sha256(key.read_bytes()).digest()


def _mac(plan: dict, timestamp: int, key: bytes) -> str:
    payload = json.dumps({"plan": plan, "issued": timestamp},
                         sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def plan(*, state_root: Path | None = None, scope: str,
         destroy_data: bool = False) -> dict:
    """Read-only reset preview. Token binds immutable Docker IDs and absolute paths."""
    root = re_lab._root(state_root)
    if not destroy_data:
        raise VMError("Data deletion must be requested using --destroy-data")
    inventory = _inventory(root, scope)
    key = _secret(root)
    issued = int(time.time())
    token = str(issued) + ":" + _mac(inventory, issued, key)
    return {"status": "PLAN_ONLY", "destructive": True, "inventory": inventory,
            "confirm_token": token, "expires_in_seconds": _WINDOW}


def recreate(*, state_root: Path | None = None, scope: str, confirm: str,
             destroy_data: bool = False, accept_license: bool = False) -> dict:
    if not destroy_data or not isinstance(confirm, str):
        raise VMError("Scoped reset requires --destroy-data and a --confirm token from dry-run")
    if scope in ("windows", "all") and not accept_license:
        raise VMError("Windows reinstall requires --accept-license before deletion")
    match = re.fullmatch(r"([0-9]{10}):([a-f0-9]{64})", confirm)
    if not match or int(time.time()) - int(match.group(1)) not in range(0, _WINDOW + 1):
        raise VMError("Scoped reset token expired or invalid")
    root = re_lab._root(state_root)
    lockfile = root / ".lock"
    if lockfile.is_symlink() or not lockfile.is_file():
        raise VMError("Untrusted RE lab lock")
    with lockfile.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise VMError("Another lab operation is running") from exc
        inventory = _inventory(root, scope)
        actual = _mac(inventory, int(match.group(1)), _secret(root))
        if not hmac.compare_digest(actual, match.group(2)):
            raise VMError("Scoped reset token no longer matches lab identity or paths")
        # No mutation occurs until the entire inventory and token are verified.
        for role in _SCOPES[scope]:
            container = re_docker_lab.inspect(role)
            if container is None or container["container_id"] != inventory["containers"][role]:
                raise VMError("Managed guest identity changed since confirmation")
        for role in _SCOPES[scope]:
            container = re_docker_lab.inspect(role)
            if container["status"] == "running":
                re_docker_lab.operate(role, "stop")
            re_lab._call(["rm", inventory["containers"][role]], timeout=45)
        for raw in inventory["deletes"]:
            target = Path(raw)
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
        # Leave the root, lock file and unrelated evidence outside scope.
    # Reuse the canonical provisioning path; on failure the reset stays explicit,
    # and the operator receives a truthful PARTIAL state rather than a success.
    try:
        if scope in ("windows", "all"):
            outcome = re_lab.setup(state_root=root, yes=True, accept_license=True)
        elif scope == "linux":
            re_lab._private(root / "workspace")
            re_lab._linux(root)
            outcome = re_lab.status(state_root=root)
        else:
            outcome = re_lab.status(state_root=root)
    except (VMError, OSError) as exc:
        return {"status": "PARTIAL", "scope": scope, "deleted": inventory["deletes"],
                "oracle_ready": False, "reason": str(exc)[:200],
                "next": "kitt re lab setup --yes --accept-license"}
    return {"status": "PROVISIONING" if scope in ("windows", "all") else "RECREATED",
            "scope": scope, "deleted": inventory["deletes"],
            "oracle_ready": False, "result": outcome}
