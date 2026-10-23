"""Host-owned Docker/Podman QEMU guest and Linux RE toolbox management.

No daemon socket mount, privileged container, global port exposure or
user-provided executable command. QEMU guest NIC is always disabled.
This module is exclusively an operator-invoked CLI entry point.
"""
from __future__ import annotations

import hashlib
import base64
try:
    import fcntl  # Linux-only VM lifecycle locking
except ImportError:
    fcntl = None
import secrets
import time
import urllib.request
import urllib.error
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile

from .vm_supervisor import VMError
from .vm_windows import _path, _sha256

VERSION = "0.86.11"
RE_EXECUTOR_VERSION = "0.88.77"
RE_TOOLBOX_VERSION = "0.88.77"
IMAGES = {
    "qemu": f"kitt/qemu-vm:{VERSION}",
    "base": f"kitt/linux-re:base-{RE_TOOLBOX_VERSION}",
    "wine": f"kitt/linux-re:wine-{RE_TOOLBOX_VERSION}",
    "full": f"kitt/linux-re:full-{RE_TOOLBOX_VERSION}",
    "ghidra": f"kitt/re-ghidra:{RE_EXECUTOR_VERSION}",
    "frida": f"kitt/re-frida:{RE_EXECUTOR_VERSION}",
}
RUNTIMES = ("docker", "podman")
PROFILE_LIMIT = 8192
NAME = re.compile(r"[a-z][a-z0-9-]{0,31}\Z")
ASSETS = Path(__file__).with_name("vm_assets")


def _runtime(name: str, *, verify: bool = True) -> str:
    if name not in RUNTIMES:
        raise VMError("Container runtime must be docker or podman")
    binary = shutil.which(name)
    if not binary:
        raise VMError(f"{name} not installed; install it or choose native QEMU")
    if verify:
        probe = subprocess.run([binary, "info"],
                               capture_output=True, timeout=20, check=False)
        if probe.returncode:
            raise VMError(f"{name} runtime is not responding to current user")
    return binary


def resolve_runtime(name: str) -> str:
    """Select a usable engine, preferring Docker for automatic selection.

    Explicit requests never fall back to a different engine. Existing VM
    profiles remain pinned to their saved engine.
    """
    if name in RUNTIMES:
        _runtime(name)
        return name
    if name != "auto":
        raise VMError("Container runtime must be auto, docker or podman")
    for candidate in RUNTIMES:
        if not shutil.which(candidate):
            continue
        try:
            _runtime(candidate)
        except (VMError, OSError, subprocess.TimeoutExpired):
            continue
        return candidate
    raise VMError(
        "No usable Docker or Podman runtime found; check docker info or "
        "podman info, or select --runtime explicitly"
    )


def _private_file(path: Path) -> Path:
    path = _path(path, exists=True)
    meta = path.stat()
    if meta.st_size > PROFILE_LIMIT or (os.name == "posix" and
        (meta.st_uid != os.getuid() or stat.S_IMODE(meta.st_mode) & 0o077)):
        raise VMError("Profile must be owned by this user and mode 0600")
    return path


def _read_profile(path: Path) -> dict:
    path = _private_file(path)
    def unique(pairs):
        value = {}
        for k, v in pairs:
            if k in value:
                raise VMError("Duplicate profile property")
            value[k] = v
        return value
    try:
        obj = json.loads(path.read_text(encoding="utf-8"),
                         object_pairs_hook=unique,
                         parse_constant=lambda _: (_ for _ in ()).throw(VMError("Nonfinite")))
    except (ValueError, UnicodeError, RecursionError) as ex:
        raise VMError("Invalid VM container profile") from ex
    fields = {"version", "name", "os", "kind", "source", "source_sha256",
              "state", "runtime", "memory_mib", "cpus", "disk_gib", "port", "image_id", "probe_port"}
    if not isinstance(obj, dict) or set(obj) != fields or obj["version"] != 1:
        raise VMError("Unknown VM profile schema")
    if (obj["os"] not in ("windows", "linux") or obj["kind"] not in ("iso", "qcow2")
            or obj["runtime"] not in RUNTIMES or not isinstance(obj["name"], str)
            or not NAME.fullmatch(obj["name"])
            or not isinstance(obj["image_id"], str)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", obj["image_id"])
            or not isinstance(obj["source_sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", obj["source_sha256"])
            or any(type(obj[k]) is not int for k in ("memory_mib", "cpus", "disk_gib", "port", "probe_port"))
            or not (1024 <= obj["memory_mib"] <= 16384 and 1 <= obj["cpus"] <= 8
                    and 16 <= obj["disk_gib"] <= 256 and 1024 <= obj["port"] <= 65535
                    and (obj["probe_port"] == 0 or 1024 <= obj["probe_port"] <= 65535)
                    and obj["probe_port"] != obj["port"])):
        raise VMError("Invalid VM profile values")
    if obj["os"] == "windows" and (obj["memory_mib"] < 4096 or obj["cpus"] < 2 or obj["disk_gib"] < 32):
        raise VMError("Windows VM requires at least 4 GiB, 2 vCPUs and 32 GiB disk")
    state = path.parent
    if _path(obj["state"]) != state:
        raise VMError("Profile state must be its own private directory")
    if state.is_symlink() or (stat.S_IMODE(state.stat().st_mode) & 0o077):
        raise VMError("VM state directory must be private")
    if obj["kind"] == "iso" and not (state / "disk.qcow2").is_file():
        source = _path(obj["source"], exists=True)
        if _sha256(source) != obj["source_sha256"]:
            raise VMError("Guest installation ISO changed since creation")
    if obj["kind"] == "qcow2" and not (state / "disk.qcow2").is_file():
        raise VMError("Imported QCOW2 disk missing")
    _private_file(state / "viewer-password")
    _private_file(state / "vnc.htpasswd")
    return obj


def build_image(*, runtime: str, image: str, profile: str = "full") -> dict:
    if image not in ("qemu", "toolbox", "ghidra", "frida"):
        raise VMError("Choose image qemu, toolbox, ghidra or frida")
    if image == "toolbox" and profile not in ("base", "wine", "full"):
        raise VMError("Unknown Linux RE toolbox profile")
    executable = _runtime(runtime)
    dockerfile = ASSETS / ("Dockerfile.qemu" if image == "qemu" else "Dockerfile.re")
    if not dockerfile.is_file():
        raise VMError("VM container assets are missing from installation")
    tag = IMAGES[profile] if image == "toolbox" else IMAGES[image]
    argv = [executable, "build", "-f", str(dockerfile), "-t", tag]
    if image == "toolbox":
        argv.extend(["--target", profile])
    elif image in ("ghidra", "frida"):
        argv.extend(["--target", image])
    argv.append(str(ASSETS))
    result = subprocess.run(argv, check=False, timeout=2400)
    if result.returncode:
        raise VMError(f"{runtime} build failed ({result.returncode})")
    return {"image": tag, "runtime": runtime, "built": True}


def pull_image(*, runtime: str, image: str, profile: str = "full") -> dict:
    """Explicitly fetch prebuilt GHCR image; never auto-pull during VM execution."""
    if image not in ("qemu", "toolbox", "ghidra", "frida"):
        raise VMError("Choose image qemu, toolbox, ghidra or frida")
    if image == "toolbox" and profile not in ("base", "wine", "full"):
        raise VMError("Unsupported RE image profile")
    executable = _runtime(runtime)
    published = ("ghcr.io/rfdetoni/kitt-qemu-vm:" + VERSION if image == "qemu"
                 else "ghcr.io/rfdetoni/kitt-linux-re:" + profile + "-" + RE_TOOLBOX_VERSION
                 if image == "toolbox" else
                 "ghcr.io/rfdetoni/kitt-re-" + image + ":" + RE_EXECUTOR_VERSION)
    local = IMAGES[profile] if image == "toolbox" else IMAGES[image]
    for argv in ([executable, "pull", published], [executable, "tag", published, local]):
        result = subprocess.run(argv, check=False, timeout=1200)
        if result.returncode:
            raise VMError("Container image pull/tag failed; build the image locally if unavailable")
    return {"pulled": published, "local_image": local, "runtime": runtime}

def _image_ready(binary: str) -> None:
    try:
        response = subprocess.run([binary, "image", "inspect", IMAGES["qemu"]],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  timeout=12, check=False)
    except (OSError, subprocess.TimeoutExpired) as ex:
        raise VMError("Could not inspect local QEMU container image") from ex
    if response.returncode:
        raise VMError(f"Build QEMU image first: kitt vm container build --runtime {Path(binary).name} --image qemu")


def _image_id(binary: str, tag: str) -> str:
    result = subprocess.run([binary, "image", "inspect", "--format", "{{.Id}}", tag],
                            capture_output=True, timeout=20, check=False)
    ref = result.stdout.decode("ascii", "replace").strip()
    if result.returncode or not re.fullmatch(r"sha256:[a-f0-9]{64}", ref):
        raise VMError("Image must have an inspectable immutable sha256 ID")
    return ref


def _viewer_auth(state: Path) -> None:
    password = secrets.token_urlsafe(32)
    credentials = state / "viewer-password"
    credentials.write_text(password + "\n", encoding="ascii")
    credentials.chmod(0o600)
    digest = base64.b64encode(hashlib.sha1(password.encode("ascii")).digest()).decode("ascii")
    auth = state / "vnc.htpasswd"
    auth.write_text("kitt:{SHA}" + digest + "\n", encoding="ascii")
    auth.chmod(0o600)


def credentials(profile_file: Path) -> dict:
    info = _read_profile(profile_file)
    password = _private_file(Path(info["state"]) / "viewer-password").read_text("ascii").strip()
    if len(password) < 32:
        raise VMError("Missing protected viewer credentials")
    return {"username": "kitt", "password": password,
            "viewer": f"http://127.0.0.1:{info['port']}/vnc.html"}


def _qcow2_validate(binary: str, path: Path, image_id: str) -> None:
    """Inspect QCOW2 inside locally built runner, without opening it as a host drive."""
    _image_ready(binary)
    argv = [
        binary, "run", "--rm", "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--mount", f"type=bind,src={path},dst=/disk.qcow2,readonly",
        "--entrypoint", "qemu-img", image_id, "info", "--output=json", "/disk.qcow2",
    ]
    result = subprocess.run(argv, capture_output=True, timeout=30, check=False)
    if result.returncode or len(result.stdout) > 65536:
        raise VMError("Unable to inspect QCOW2 using trusted runner")
    try:
        obj = json.loads(result.stdout)
    except (ValueError, UnicodeError) as ex:
        raise VMError("Malformed qemu-img output") from ex
    if (not isinstance(obj, dict) or obj.get("format") != "qcow2"
            or obj.get("backing-filename") or obj.get("backing-filename-format")
            or obj.get("data-file") or obj.get("data-file-raw") or obj.get("encrypted")):
        raise VMError("Only standalone, unencrypted QCOW2 images can be imported")


def prepare_vm(*, runtime: str, system: str, source: Path, destination: Path,
               kind: str, memory_mib: int = 4096, cpus: int = 2,
               disk_gib: int = 64, port: int = 18006, probe_port: int = 0) -> Path:
    if sys.platform != "linux":
        raise VMError("QEMU container backend requires Linux host")
    binary = _runtime(runtime)
    _image_ready(binary)
    image_id = _image_id(binary, IMAGES["qemu"])
    if system not in ("windows", "linux") or kind not in ("iso", "qcow2"):
        raise VMError("Unsupported guest format")
    if any(type(x) is not int for x in (memory_mib, cpus, disk_gib, port, probe_port)):
        raise VMError("CPU/memory/disk/port must be integers")
    if not (1024 <= memory_mib <= 16384 and 1 <= cpus <= 8
            and 16 <= disk_gib <= 256 and 1024 <= port <= 65535
            and (probe_port == 0 or 1024 <= probe_port <= 65535)
            and probe_port != port):
        raise VMError("Container guest resource limits exceeded")
    if system == "windows" and (memory_mib < 4096 or cpus < 2 or disk_gib < 32):
        raise VMError("Windows requires >=4 GiB RAM, >=2 CPU, >=32 GiB disk")
    src, dest = _path(source, exists=True), _path(destination)
    if src.suffix.lower() != "." + kind or dest.exists():
        raise VMError("Source extension mismatch or VM destination already exists")
    if src.is_relative_to(dest):
        raise VMError("Source image cannot be inside guest state directory")
    parent = dest.parent
    parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    if (parent.is_symlink() or parent.stat().st_uid != os.getuid()
            or stat.S_IMODE(parent.stat().st_mode) & 0o077):
        raise VMError("VM destination parent must be owned by user and mode 0700")
    if kind == "qcow2":
        _qcow2_validate(binary, src, image_id)
    with tempfile.TemporaryDirectory(dir=parent, prefix=".kitt-container-") as temp:
        stage = Path(temp)
        if kind == "qcow2":
            shutil.copyfile(src, stage / "disk.qcow2")
            (stage / "disk.qcow2").chmod(0o600)
        name = "kitt-vm-" + hashlib.sha256(str(dest).encode()).hexdigest()[:12]
        profile = {
            "version": 1, "name": name, "os": system, "kind": kind,
            "source": str(src), "source_sha256": _sha256(src),
            "state": str(dest), "runtime": runtime, "image_id": image_id,
            "memory_mib": memory_mib, "cpus": cpus,
            "disk_gib": disk_gib, "port": port, "probe_port": probe_port,
        }
        _viewer_auth(stage)
        (stage / "profile.json").write_text(json.dumps(profile, indent=2) + "\n")
        (stage / "profile.json").chmod(0o600)
        stage.rename(dest)
    return dest / "profile.json"


def _run_args(profile: dict, *, binary: str, install: bool, allow_tcg: bool) -> list[str]:
    if type(install) is not bool or type(allow_tcg) is not bool:
        raise VMError("Invalid guest operation")
    if install and profile["kind"] != "iso":
        raise VMError("--install requires a user-provided ISO")
    has_kvm = os.access("/dev/kvm", os.R_OK | os.W_OK)
    if not has_kvm and not allow_tcg:
        raise VMError("No accessible /dev/kvm; use native QEMU or explicit --allow-tcg")
    state = Path(profile["state"])
    args = [
        binary, "run", "-d", "--name", profile["name"],
        "--pull", "never",
        "--cap-drop=ALL", "--security-opt=no-new-privileges",
        "--pids-limit", "256", "--memory", f"{profile['memory_mib'] + 1024}m",
        "--cpus", str(profile["cpus"] + 1),
        "--read-only", "--tmpfs", "/tmp:rw,nosuid,size=64m",
        "--network", "bridge",
        "-p", f"127.0.0.1:{profile['port']}:8006",
        "--user", f"{os.getuid()}:{os.getgid()}",
        "--mount", f"type=bind,src={state},dst=/state",
        "--mount", f"type=bind,src={state / 'vnc.htpasswd'},dst=/run/secrets/kitt-vnc.htpasswd,readonly",
    ]
    if profile["probe_port"]:
        args.extend(["-p", f"127.0.0.1:{profile['probe_port']}:2222"])
    if profile["runtime"] == "podman":
        args.extend(["--userns=keep-id", "--group-add=keep-groups"])
    if has_kvm:
        args.extend(["--device", "/dev/kvm"])
        if profile["runtime"] == "docker":
            args.extend(["--group-add", str(Path("/dev/kvm").stat().st_gid)])
    if install:
        args.extend(["--mount", f"type=bind,src={profile['source']},dst=/input/installer.iso,readonly"])
    args.extend([
        "--env", f"KITT_GUEST_OS={profile['os']}",
        "--env", f"KITT_SOURCE_KIND={profile['kind']}",
        "--env", f"KITT_MEMORY_MIB={profile['memory_mib']}",
        "--env", f"KITT_CPUS={profile['cpus']}",
        "--env", f"KITT_DISK_GIB={profile['disk_gib']}",
        "--env", f"KITT_INSTALL={int(install)}",
        "--env", f"KITT_PROBE_SSH={int(bool(profile['probe_port']))}",
        profile["image_id"],
    ])
    return args


def _status(binary: str, name: str) -> str:
    result = subprocess.run(
        [binary, "inspect", "--format", "{{.State.Status}}", name],
        capture_output=True, timeout=15, check=False,
    )
    if result.returncode:
        return "missing"
    value = result.stdout.decode("ascii", "replace").strip()
    if value not in ("created", "running", "exited", "dead", "paused", "restarting"):
        raise VMError("Unexpected QEMU container status")
    return value


def _archive_logs(binary: str, profile: dict) -> None:
    """Preserve bounded evidence before deleting a stopped container."""
    result = subprocess.run([binary, "logs", "--tail", "500", profile["name"]],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            timeout=20, check=False)
    data = result.stdout[-128 * 1024:] if result.returncode == 0 else b""
    if data:
        path = Path(profile["state"]) / "previous-run.log"
        with path.open("wb") as stream:
            stream.write(data)
        path.chmod(0o600)


def _http_ready(port: int, password: str) -> bool:
    headers = {"Authorization": "Basic " + base64.b64encode(
        f"kitt:{password}".encode("ascii")).decode("ascii")}
    req = urllib.request.Request(f"http://127.0.0.1:{port}/vnc.html",
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=1.5) as response:
            return response.status == 200 and "html" in response.headers.get("Content-Type", "")
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def start_vm(profile_file: Path, *, install: bool = False, allow_tcg: bool = False) -> dict:
    profile = _read_profile(profile_file)
    if sys.platform != "linux" or fcntl is None:
        raise VMError("Container QEMU requires Linux host")
    binary = _runtime(profile["runtime"])
    if _image_id(binary, profile["image_id"]) != profile["image_id"]:
        raise VMError("Pinned image ID is unavailable")
    state = Path(profile["state"])
    if install:
        source = _path(profile["source"], exists=True)
        if _sha256(source) != profile["source_sha256"]:
            raise VMError("Installation ISO changed")
    password = _private_file(state / "viewer-password").read_text("ascii").strip()
    with (state / ".lifecycle.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        status = _status(binary, profile["name"])
        if status in ("running", "paused", "restarting"):
            raise VMError(f"Guest already active ({status}); refuse concurrent disk access")
        if status != "missing":
            _archive_logs(binary, profile)
            result = subprocess.run([binary, "rm", profile["name"]],
                                    capture_output=True, timeout=30, check=False)
            if result.returncode:
                raise VMError("Cannot remove stopped guest container before restarting")
        argv = _run_args(profile, binary=binary, install=install, allow_tcg=allow_tcg)
        result = subprocess.run(argv, capture_output=True, timeout=45, check=False)
        if result.returncode:
            raise VMError(f"Container could not start (code={result.returncode}): "
                          + result.stderr.decode("utf-8", "replace")[:300])
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            status = _status(binary, profile["name"])
            if status != "running":
                if status == "created":
                    time.sleep(0.5)
                    continue
                _archive_logs(binary, profile)
                raise VMError(f"QEMU/noVNC process exited before ready ({status})")
            if _http_ready(profile["port"], password):
                return {"name": profile["name"], "runtime": profile["runtime"],
                        "viewer": f"http://127.0.0.1:{profile['port']}/vnc.html",
                        "credentials": f"kitt vm container credentials --profile {profile_file}",
                        "status": "ready"}
            time.sleep(0.5)
        subprocess.run([binary, "stop", "-t", "5", profile["name"]],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=15, check=False)
        _archive_logs(binary, profile)
        raise VMError("QEMU web console readiness timeout; inspect previous-run.log")


def operate(profile_file: Path, action: str) -> dict:
    if action not in ("stop", "logs", "status"):
        raise VMError("Unsupported container action")
    profile = _read_profile(profile_file)
    binary = _runtime(profile["runtime"])
    status = _status(binary, profile["name"])
    if action == "status":
        return {"name": profile["name"], "status": status}
    if action == "logs" and status == "missing":
        path = Path(profile["state"]) / "previous-run.log"
        output = path.read_bytes()[-16000:].decode("utf-8", "replace") if path.is_file() else ""
        return {"name": profile["name"], "action": "logs", "output": output}
    if action == "stop" and status != "running":
        return {"name": profile["name"], "action": "stop", "status": status}
    argv = [binary, "stop", "-t", "20", profile["name"]] if action == "stop" else [
        binary, "logs", "--tail", "120", profile["name"]]
    result = subprocess.run(argv, capture_output=True, timeout=50, check=False)
    if result.returncode:
        raise VMError(f"VM container {action} failed (exit {result.returncode})")
    if action == "stop":
        _archive_logs(binary, profile)
    return {"name": profile["name"], "action": action,
            "output": result.stdout.decode("utf-8", "replace")[:16000]}



def _snapshot_component(path: Path, *, directory: bool) -> bool:
    """Refuse symlinks and special files in offline VM state before copying."""
    if path.is_symlink():
        raise VMError("Snapshot component cannot be a symlink")
    if not path.exists():
        return False
    if directory:
        if not path.is_dir():
            raise VMError("Expected VM state directory")
        for root, folders, files in os.walk(path, followlinks=False):
            for name in (*folders, *files):
                child = Path(root) / name
                if child.is_symlink() or not (child.is_file() or child.is_dir()):
                    raise VMError("Unsafe file in VM state directory")
    elif not path.is_file():
        raise VMError("Expected VM state file")
    return True


def snapshot(profile_file: Path, *, action: str, name: str = "baseline") -> dict:
    """Offline snapshot and restore of guest disk, UEFI and virtual TPM state."""
    if action not in ("save", "restore", "list") or not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", name):
        raise VMError("Invalid snapshot action or name")
    if sys.platform != "linux" or fcntl is None:
        raise VMError("Container VM snapshots require Linux host")
    profile = _read_profile(profile_file)
    state = Path(profile["state"])
    binary = _runtime(profile["runtime"])
    directory = state / "snapshots"
    if directory.is_symlink():
        raise VMError("Snapshot directory may not be a symlink")
    if action == "list":
        return {"snapshots": sorted(p.name for p in directory.iterdir()
                if p.is_dir() and not p.is_symlink()) if directory.is_dir() else []}
    directory.mkdir(mode=0o700, exist_ok=True)
    target = directory / name
    with (state / ".lifecycle.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if _status(binary, profile["name"]) in ("running", "paused", "restarting", "created"):
            raise VMError("Shut down the guest before snapshot/restore")
        source = state / "disk.qcow2"
        if not source.is_file() or source.is_symlink():
            raise VMError("Guest disk missing")
        if action == "save":
            if target.exists() or target.is_symlink():
                raise VMError("Snapshot already exists")
            with tempfile.TemporaryDirectory(prefix=".snapshot-", dir=directory) as temporary:
                staging = Path(temporary)
                shutil.copyfile(source, staging / "disk.qcow2")
                (staging / "disk.qcow2").chmod(0o600)
                if _snapshot_component(state / "OVMF_VARS.fd", directory=False):
                    shutil.copyfile(state / "OVMF_VARS.fd", staging / "OVMF_VARS.fd")
                if _snapshot_component(state / "tpm", directory=True):
                    shutil.copytree(state / "tpm", staging / "tpm", symlinks=True)
                    _snapshot_component(staging / "tpm", directory=True)
                digest = _sha256(staging / "disk.qcow2")
                (staging / "snapshot.json").write_text(
                    json.dumps({"schema": 1, "disk_sha256": digest,
                                "runner_image": profile["image_id"]}) + "\n")
                staging.rename(target)
            return {"saved": str(target), "disk_sha256": digest}
        if not target.is_dir() or target.is_symlink():
            raise VMError("Snapshot not found")
        metadata = target / "snapshot.json"
        if not metadata.is_file() or metadata.is_symlink() or metadata.stat().st_size > 2048:
            raise VMError("Snapshot metadata unavailable")
        record = json.loads(metadata.read_text("utf-8"))
        expected = {"schema", "disk_sha256", "runner_image"}
        if (not isinstance(record, dict) or set(record) != expected
                or record["schema"] != 1 or record["runner_image"] != profile["image_id"]
                or not isinstance(record["disk_sha256"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", record["disk_sha256"])
                or (target / "disk.qcow2").is_symlink()
                or _sha256(target / "disk.qcow2") != record["disk_sha256"]):
            raise VMError("Snapshot integrity/runner mismatch")
        # Validate optional state before replacing the disk: a bad TPM/UEFI tree
        # must not leave the restored VM with a partially replaced disk.
        saved_uefi = target / "OVMF_VARS.fd"
        saved_tpm = target / "tpm"
        restore_uefi = _snapshot_component(saved_uefi, directory=False)
        restore_tpm = _snapshot_component(saved_tpm, directory=True)
        _snapshot_component(state / "OVMF_VARS.fd", directory=False)
        _snapshot_component(state / "tpm", directory=True)
        # Disk replacement is atomic on the same volume; TPM/UEFI are copied offline.
        with tempfile.TemporaryDirectory(prefix=".restore-", dir=state) as temporary:
            nextdisk = Path(temporary) / "disk.qcow2"
            shutil.copyfile(target / "disk.qcow2", nextdisk)
            nextdisk.chmod(0o600)
            os.replace(nextdisk, source)
        if restore_uefi:
            shutil.copyfile(saved_uefi, state / "OVMF_VARS.fd")
        if restore_tpm:
            replacement = state / "tpm"
            if replacement.exists():
                shutil.rmtree(replacement)
            shutil.copytree(saved_tpm, replacement, symlinks=True)
        return {"restored": str(target), "disk_sha256": record["disk_sha256"]}


def doctor(runtime: str) -> dict:
    binary = _runtime(runtime)
    kvm = os.access("/dev/kvm", os.R_OK | os.W_OK)
    image = subprocess.run([binary, "image", "inspect", IMAGES["qemu"]],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
    return {"runtime": runtime, "ready": True, "kvm_available": kvm,
            "qemu_image_built": image.returncode == 0,
            "rootless": runtime == "podman" and os.geteuid() != 0,
            "guest_network_offline": True, "browser_requires_container_bridge": True}


def toolbox(*, runtime: str, profile: str, workspace: Path,
            allow_ptrace: bool = False) -> int:
    if profile not in ("base", "wine", "full", "ghidra", "frida"):
        raise VMError("Invalid Linux RE profile")
    if allow_ptrace and profile != "frida":
        raise VMError("Ptrace can only be enabled for an isolated Frida executor")
    binary = _runtime(runtime)
    pinned_toolbox = _image_id(binary, IMAGES[profile])
    path = _path(workspace)
    if not path.is_dir() or path.is_symlink():
        raise VMError("Workspace must be an existing directory")
    if not sys.stdin.isatty():
        raise VMError("Toolbox shell requires a real terminal")
    argv = [binary, "run", "--rm", "-it", "--pull", "never",
            "--network", "none", "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "--user", f"{os.getuid()}:{os.getgid()}",
            # Ghidra and REA need writable cache/home even for an arbitrary UID.
            # These paths are ephemeral; never mount host secrets or user HOME.
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=1g,mode=1777",
            "--env", "HOME=/tmp", "--env", "XDG_CACHE_HOME=/tmp/.cache",
            "--env", "XDG_CONFIG_HOME=/tmp/.config",
            "--mount", f"type=bind,src={path},dst=/workspace" +
                       (",readonly" if profile in ("ghidra", "frida") else ""),
            "--workdir", "/workspace", pinned_toolbox, "/bin/sh"]
    if allow_ptrace:
        # Operator opt-in; no host PID namespace, privileged flag, or host socket.
        argv[2:2] = ["--cap-add=SYS_PTRACE"]
    if runtime == "podman":
        argv[2:2] = ["--userns=keep-id"]
    return subprocess.run(argv, check=False).returncode
