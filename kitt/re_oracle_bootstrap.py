"""Explicit host-owned disposable Dockur Windows Oracle bootstrap.

Requires --yes --accept-license; existing Windows is NEVER replaced except with
--recreate. Downloads/install occur only when the operator invokes bootstrap.
Secrets stay under the private RE lab, outside the model-writable workspace.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess

from kitt import re_docker_lab
from kitt.vm_supervisor import VMError

ASSETS = Path(__file__).with_name("oracle_assets")
HOST_PORT = 2222
MANAGED = "kitt-re-win11"


def _run(argv: list[str], *, timeout: int = 60) -> str:
    try:
        result = subprocess.run(argv, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VMError("Oracle bootstrap host command failed or timed out") from exc
    if result.returncode:
        # Avoid echoing command output: an invoked subprocess may disclose keys.
        raise VMError("Oracle bootstrap command failed: " + Path(argv[0]).name)
    if len(result.stdout) > 65536:
        raise VMError("Oracle bootstrap command output exceeds size limit")
    return result.stdout.decode("utf-8", "replace").strip()


def _dir(path: Path) -> None:
    if path.is_symlink():
        raise VMError("Oracle bootstrap directory may not be a symlink")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise VMError("Oracle bootstrap directory must be private and owned by operator")


def _write(path: Path, data: bytes) -> None:
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file():
            raise VMError("Refusing to overwrite nonregular Oracle bootstrap file")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
    path.chmod(0o600)


def _key(path: Path) -> None:
    if path.exists():
        if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise VMError("Untrusted existing Oracle key")
        if not path.with_suffix(path.suffix + ".pub").is_file():
            raise VMError("Oracle key is missing its public half")
        return
    executable = shutil.which("ssh-keygen")
    if not executable:
        raise VMError("OpenSSH ssh-keygen is required on Linux")
    _run([executable, "-q", "-t", "ed25519", "-N", "", "-f", str(path)], timeout=20)
    path.chmod(0o600)


def _lab() -> tuple[Path, dict]:
    linux = re_docker_lab.inspect("linux")
    if linux is None or linux["status"] != "running":
        raise VMError("Start the existing managed KITT full Linux RE toolbox first")
    lab = Path(linux["workspace"]).parent
    re_docker_lab._lab_root(str(lab))
    if (lab / "windows").is_symlink() or not (lab / "windows").is_dir():
        raise VMError("Missing or unsafe Dockur Windows storage")
    return lab, linux


def _compile_probe(lab: Path, linux: dict, stage: Path) -> bytes:
    work = lab / "workspace" / ".kitt-oracle"
    _dir(work)
    source = ASSETS / "event_probe.c"
    if not source.is_file():
        raise VMError("Agent Oracle assets are missing")
    _write(work / "event_probe.c", source.read_bytes())
    # Only an authored, fixed C source in the isolated RE container.
    _run([shutil.which("docker") or "docker", "exec", linux["container_id"],
          "x86_64-w64-mingw32-gcc", "-std=c11", "-O2", "-Wall", "-Wextra",
          "-o", "/workspace/.kitt-oracle/event_probe.exe",
          "/workspace/.kitt-oracle/event_probe.c"], timeout=90)
    binary = work / "event_probe.exe"
    if not binary.is_file() or binary.is_symlink() or not 128 < binary.stat().st_size <= 8 * 1024 * 1024:
        raise VMError("MinGW did not generate a bounded Windows x64 probe")
    data = binary.read_bytes()
    if not data.startswith(b"MZ"):
        raise VMError("MinGW output is not a Windows PE executable")
    _write(stage / "event_probe.exe", data)
    return data


def prepare() -> dict:
    """Prepare immutable keys, pinned binary, OEM and SSH profile before mutation."""
    lab, linux = _lab()
    oracle = lab / "oracle"
    stage = oracle / "oem"
    _dir(oracle)
    _dir(stage)
    _key(oracle / "client_ed25519")
    _key(oracle / "ssh_host_ed25519_key")
    binary = _compile_probe(lab, linux, stage)
    expected = hashlib.sha256(binary).hexdigest()
    manifest = (json.dumps({"probes": {"event_auto_reset": {
        "path": "event_probe.exe", "sha256": expected,
    }}}, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    for name in ("install.bat", "kitt-bootstrap.ps1", "kitt-guest-rpc.ps1"):
        item = ASSETS / name
        if not item.is_file() or item.stat().st_size > 65536:
            raise VMError("Missing or invalid Oracle OEM setup asset")
        _write(stage / name, item.read_bytes())
    _write(stage / "manifest.json", manifest)
    _write(stage / "client_ed25519.pub", (oracle / "client_ed25519.pub").read_bytes())
    _write(stage / "ssh_host_ed25519_key", (oracle / "ssh_host_ed25519_key").read_bytes())
    host_public = (oracle / "ssh_host_ed25519_key.pub").read_text("ascii").strip().split()
    if len(host_public) < 2 or host_public[0] != "ssh-ed25519" or not re.fullmatch(r"[a-zA-Z0-9+/=]+", host_public[1]):
        raise VMError("Cannot derive pinned Windows Oracle host identity")
    _write(oracle / "known_hosts",
           f"[127.0.0.1]:{HOST_PORT} ssh-ed25519 {host_public[1]}\n".encode("ascii"))
    config = (f"Host kitt-win-oracle\n  HostName 127.0.0.1\n  Port {HOST_PORT}\n"
              f"  User kittprobe\n  IdentityFile {oracle / 'client_ed25519'}\n"
              f"  UserKnownHostsFile {oracle / 'known_hosts'}\n"
              "  IdentitiesOnly yes\n  StrictHostKeyChecking yes\n"
              "  BatchMode yes\n  ForwardAgent no\n  RequestTTY no\n"
              "  ConnectionAttempts 1\n")
    _write(oracle / "ssh_config", config.encode("utf-8"))
    hosts = {"schema_version": 1, "ssh_config": str(oracle / "ssh_config"),
             "reference": "kitt-win-oracle", "candidate": "kitt-wine-guest"}
    _write(oracle / "hosts.json", (json.dumps(hosts, indent=2) + "\n").encode())
    digest = hashlib.sha256(manifest).hexdigest()
    _write(oracle / "manifest.sha256", (digest + "\n").encode("ascii"))
    return {"lab": str(lab), "oem": str(stage), "hosts": str(oracle / "hosts.json"),
            "manifest_sha256": digest, "probe_sha256": expected}


def _remove_disk(storage: Path, lab: Path) -> None:
    if storage != lab / "windows" or storage.is_symlink() or not storage.is_dir():
        raise VMError("Refusing unsafe Windows disk removal")
    if storage.resolve(strict=True) != lab / "windows":
        raise VMError("Windows storage path has changed")
    for path in storage.rglob("*"):
        if path.is_symlink():
            raise VMError("Refusing Windows disk reset with unexpected symbolic links")
    shutil.rmtree(storage)
    _dir(storage)


def bootstrap(*, recreate: bool, yes: bool, accept_license: bool) -> dict:
    if not yes or not accept_license:
        raise VMError("Oracle bootstrap needs --yes and --accept-license")
    lab, _ = _lab()
    import fcntl as _fcntl
    with (lab / ".lock").open("a+") as lock:
        _fcntl.flock(lock.fileno(), _fcntl.LOCK_EX)
        existing = re_docker_lab.inspect("windows")
        if existing is not None and not recreate:
            raise VMError("Existing Dockur Windows must be explicitly recreated (--recreate)")
        plan = prepare()
        credentials = lab / "windows.env"
        if credentials.is_symlink() or not credentials.is_file():
            raise VMError("Expected private installer-managed Windows credentials not found")
        if stat.S_IMODE(credentials.stat().st_mode) & 0o077:
            raise VMError("Windows credentials have unsafe permissions")
        storage = lab / "windows"
        if existing:
            _run(["docker", "stop", "-t", "120", existing["container_id"]], timeout=145)
            _run(["docker", "rm", existing["container_id"]], timeout=30)
        if existing or recreate:
            _remove_disk(storage, lab)
        elif any(storage.iterdir()):
            raise VMError("Existing Windows disk without a managed container; no deletion")
        # Pin existing, installed Dockur image; never switch image mid-run.
        image_id = _run(["docker", "image", "inspect", "--format", "{{.Id}}", "dockurr/windows"])
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
            raise VMError("Pull and inspect the official dockurr/windows image first")
        args = ["docker", "create", "--name", MANAGED, "--restart", "unless-stopped",
                "--device", "/dev/kvm", "--device", "/dev/net/tun", "--cap-add", "NET_ADMIN",
                "--stop-timeout", "120", "--env-file", str(credentials),
                "--env", "USER_PORTS=22",
                "--publish", "127.0.0.1:8006:8006",
                "--publish", f"127.0.0.1:{HOST_PORT}:22",
                "--mount", f"type=bind,src={storage},dst=/storage",
                "--mount", f"type=bind,src={plan['oem']},dst=/oem,readonly",
                "--label", "dev.kitt.re-lab=v1", "--label", "dev.kitt.re-role=windows",
                "--label", f"dev.kitt.re-root={lab}", image_id]
        _run(args)
        _run(["docker", "start", MANAGED], timeout=90)
        return {"status": "PROVISIONING", "windows_url": "http://127.0.0.1:8006",
                "ssh_port": HOST_PORT, "hosts": plan["hosts"],
                "manifest_sha256": plan["manifest_sha256"],
                "next": "kitt re oracle doctor --hosts <hosts> --manifest-sha256 <sha256>",
                "oracle_ready": False}
