"""Host-owned, explicit Docker/KVM reverse-engineering lab lifecycle.

The only Windows guest image is upstream dockurr/windows. Mutations are never
reachable through the model-facing tool registry; the operator invokes this CLI.
"""
from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import time

from kitt import re_docker_lab, re_oracle, re_oracle_bootstrap
from kitt.vm_supervisor import VMError

DEFAULT_ROOT = Path.home() / ".local" / "share" / "kitt" / "re-lab"
_DOCKUR = "docker.io/dockurr/windows"
_HEX = re.compile(r"sha256:[0-9a-f]{64}\Z")
_DIGEST = re.compile(r"(?:docker.io/)?dockurr/windows@sha256:[0-9a-f]{64}\Z")


def _call(args: list[str], *, timeout: int = 30) -> str:
    result = re_docker_lab._docker(args, timeout=timeout)
    if result.returncode or len(result.stdout) > 65536:
        raise VMError("Docker RE lab operation failed: " + " ".join(args[:2]))
    return result.stdout.decode("utf-8", "replace").strip()


def _root(path: Path | None) -> Path:
    root = (path or DEFAULT_ROOT).expanduser().absolute()
    if root.name != "re-lab" or not root.is_relative_to(Path.home().resolve()):
        raise VMError("RE lab must be a private re-lab directory beneath the operator home")
    if root.exists():
        re_docker_lab._lab_root(str(root))
    return root


def _private(path: Path) -> None:
    re_oracle_bootstrap._dir(path)
    if path.resolve(strict=True) != path:
        raise VMError("RE lab path includes a symbolic link")


def _memory_available() -> int:
    try:
        for line in Path("/proc/meminfo").read_text("ascii").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def doctor(*, state_root: Path | None = None) -> dict:
    """Read-only preflight. No pulls, installs, guest commands, or new directories."""
    root = _root(state_root)
    checks = {
        "linux": sys.platform == "linux",
        "docker_cli": shutil.which("docker") is not None,
        "kvm": os.access("/dev/kvm", os.R_OK | os.W_OK),
        "tun": os.path.exists("/dev/net/tun"),
        "memory": _memory_available() >= 6 * 1024 ** 3,
        "disk": False,
        "docker_engine": False,
    }
    directory = root
    while not directory.exists():
        directory = directory.parent
    try:
        checks["disk"] = shutil.disk_usage(directory).free >= 40 * 1024 ** 3
    except OSError:
        pass
    if checks["linux"] and checks["docker_cli"]:
        try:
            _call(["info", "--format", "{{.ServerVersion}}"], timeout=20)
            checks["docker_engine"] = True
        except VMError:
            pass
    return {
        "status": "READY" if all(checks.values()) else "BLOCKED_ENV",
        "ready_for_setup": all(checks.values()),
        "checks": checks,
        "root": str(root),
        "requires_explicit_windows_license": True,
        "windows_oracle_verified": False,
    }


def status(*, state_root: Path | None = None) -> dict:
    root = _root(state_root)
    guests = {}
    for role in ("linux", "windows"):
        try:
            guests[role] = re_docker_lab.inspect(role) or {"status": "missing"}
        except (VMError, OSError) as exc:
            guests[role] = {"status": "BLOCKED_ENV", "reason": str(exc)[:160]}
    if root.exists():
        _root(root)
    manifest = root / "oracle" / "manifest.sha256"
    pinned = (manifest.is_file() and not manifest.is_symlink()
              and stat.S_IMODE(manifest.stat().st_mode) & 0o077 == 0)
    return {
        "root": str(root), "guests": guests, "bootstrap_prepared": pinned,
        "ssh": "UNVERIFIED", "oracle_ready": False, "wine": "UNVERIFIED",
        "probe": "NOT_RUN", "note": "Use verify to perform live authenticated checks",
    }


def _image_lock(root: Path) -> str:
    """Require upstream registry digest and store the exact local content ID."""
    _call(["pull", _DOCKUR], timeout=1800)
    image_id = _call(["image", "inspect", "--format", "{{.Id}}", _DOCKUR])
    if not _HEX.fullmatch(image_id):
        raise VMError("Dockur image has no immutable content ID")
    raw = _call(["image", "inspect", "--format", "{{json .RepoDigests}}", _DOCKUR])
    try:
        digests = json.loads(raw)
    except ValueError as exc:
        raise VMError("Cannot verify upstream Dockur image digest") from exc
    if not isinstance(digests, list):
        raise VMError("Cannot verify upstream Dockur repository identity")
    upstream = sorted(x for x in digests if isinstance(x, str) and _DIGEST.fullmatch(x))
    if not upstream:
        raise VMError("Docker image does not have an official dockurr/windows repository digest")
    lock = root / "oracle" / "image.lock.json"
    record = {"image_id": image_id, "repo_digest": upstream[0]}
    if lock.exists() or lock.is_symlink():
        if lock.is_symlink() or not lock.is_file() or stat.S_IMODE(lock.stat().st_mode) & 0o077:
            raise VMError("Untrusted Windows image lock")
        try:
            previous = json.loads(lock.read_text("utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise VMError("Corrupt Windows image lock") from exc
        if previous != record:
            raise VMError("Dockur digest changed; operator must approve an explicit image upgrade")
    else:
        data = (json.dumps(record, sort_keys=True) + "\n").encode("ascii")
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
    return image_id


def _credentials(root: Path) -> None:
    path = root / "windows.env"
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise VMError("Unsafe existing Windows credentials")
        content = path.read_text("ascii")
        if ("PASSWORD=admin" in content or "USERNAME=Docker" in content
                or "AUTOLOGIN=Y" in content):
            raise VMError("Legacy Windows credentials require an explicit migration, not reuse")
        return
    if any((root / "windows").iterdir()):
        raise VMError("Existing Windows disk has no matching credentials; refusing adoption")
    content = ("VERSION=11\nUSERNAME=KittLab\nPASSWORD=" + secrets.token_urlsafe(36)
               + "\nAUTOLOGIN=N\nRAM_SIZE=6G\nCPU_CORES=2\nDISK_SIZE=80G\nSAMBA=N\n")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as stream:
        stream.write(content)


def _linux(root: Path) -> None:
    existing = re_docker_lab.inspect("linux")
    if existing is not None:
        if Path(existing["workspace"]).parent != root:
            raise VMError("Existing Linux lab belongs to another root; refusing takeover")
        re_docker_lab.operate("linux", "start")
        return
    from kitt import vm_container
    try:
        vm_container.pull_image(runtime="docker", image="toolbox", profile="full")
    except VMError:
        vm_container.build_image(runtime="docker", image="toolbox", profile="full")
    image = vm_container._image_id(shutil.which("docker") or "docker", vm_container.IMAGES["full"])
    workspace = root / "workspace"
    _call(["create", "--name", "kitt-re-full", "--restart", "unless-stopped",
           "--network", "none", "--cap-drop", "ALL",
           "--security-opt", "no-new-privileges",
           "--user", str(os.getuid()) + ":" + str(os.getgid()),
           "--tmpfs", "/tmp:rw,nosuid,nodev,size=1g,mode=1777",
           "--env", "HOME=/tmp", "--env", "XDG_CACHE_HOME=/tmp/.cache",
           "--env", "XDG_CONFIG_HOME=/tmp/.config",
           "--mount", "type=bind,src=" + str(workspace) + ",dst=/workspace",
           "--workdir", "/workspace",
           "--label", "dev.kitt.re-lab=v1", "--label", "dev.kitt.re-role=toolbox",
           "--label", "dev.kitt.re-root=" + str(root),
           image, "sleep", "infinity"], timeout=120)
    re_docker_lab.operate("linux", "start")


def setup(*, state_root: Path | None = None, runtime: str = "docker",
          windows: str = "11", linux_profile: str = "full",
          yes: bool = False, accept_license: bool = False) -> dict:
    """Create and start owned guests, preserving all existing disk and identities."""
    if (runtime, windows, linux_profile) != ("docker", "11", "full"):
        raise VMError("Only Docker, Windows 11, Linux RE full are currently supported")
    if not yes or not accept_license:
        raise VMError("Setup requires --yes and --accept-license (Windows license)")
    preflight = doctor(state_root=state_root)
    if not preflight["ready_for_setup"]:
        raise VMError("RE lab preflight blocked: " + ", ".join(
            key for key, ok in preflight["checks"].items() if not ok))
    root = _root(state_root)
    _private(root)
    for folder in ("workspace", "windows", "oracle"):
        _private(root / folder)
    with (root / ".lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        original = re_docker_lab.inspect("windows")
        if original is not None:
            existing_root = Path(re_docker_lab.inspect("linux")["workspace"]).parent if re_docker_lab.inspect("linux") else None
            if existing_root != root:
                raise VMError("Windows already exists; lab root mismatch")
            return {**status(state_root=root), "status": "EXISTING_UNVERIFIED",
                    "message": "Existing Windows preserved; run verify to check the Oracle"}
        if any((root / "windows").iterdir()):
            raise VMError("Nonempty Windows disk without validated container; no overwrite")
        _linux(root)
        _credentials(root)
        image_id = _image_lock(root)
        result = re_oracle_bootstrap.bootstrap(recreate=False, yes=True, accept_license=True)
        return {**result, "root": str(root), "image_id": image_id, "status": "PROVISIONING",
                "oracle_ready": False, "wine": "UNVERIFIED"}


def operate(action: str, *, state_root: Path | None = None) -> dict:
    if action not in {"up", "down"}:
        raise VMError("Unsupported lab operation")
    root = _root(state_root)
    systems = ("linux", "windows") if action == "up" else ("windows", "linux")
    updates = {}
    for role in systems:
        guest = re_docker_lab.inspect(role)
        if guest is None:
            updates[role] = {"status": "missing"}
        else:
            if role == "linux" and Path(guest["workspace"]).parent != root:
                raise VMError("Linux guest root mismatch")
            updates[role] = re_docker_lab.operate(role, "start" if action == "up" else "stop")
    return {"status": action.upper(), "guests": updates, "oracle_ready": False}


def verify(*, state_root: Path | None = None, full: bool = False) -> dict:
    root = _root(state_root)
    oracle_dir = root / "oracle"
    manifest_file = oracle_dir / "manifest.sha256"
    if (manifest_file.is_symlink() or not manifest_file.is_file()
            or stat.S_IMODE(manifest_file.stat().st_mode) & 0o077):
        return {"status": "BLOCKED_ENV", "oracle_ready": False,
                "reason": "No operator-owned pinned Oracle manifest"}
    digest = manifest_file.read_text("ascii").strip()
    checked = re_oracle.doctor(manifest_sha256=digest, hosts=oracle_dir / "hosts.json")
    if not checked["oracle_ready"] or not full:
        return checked
    from kitt import re_oracle_pilot
    pilot = re_oracle_pilot.pilot(manifest_sha256=digest, hosts=oracle_dir / "hosts.json",
                                 yes=True)
    return {**checked, "pilot": pilot, "status": pilot["status"],
            "wine_isolated": False, "lab_ready": False}
