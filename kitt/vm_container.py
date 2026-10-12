"""Host-owned Docker/Podman QEMU guest and Linux RE toolbox management.

No daemon socket mount, privileged container, global port exposure or
user-provided executable command. QEMU guest NIC is always disabled.
This module is exclusively an operator-invoked CLI entry point.
"""
from __future__ import annotations

import hashlib
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

VERSION = "0.86.8"
IMAGES = {
    "qemu": f"kitt/qemu-vm:{VERSION}",
    "base": f"kitt/linux-re:base-{VERSION}",
    "wine": f"kitt/linux-re:wine-{VERSION}",
    "full": f"kitt/linux-re:full-{VERSION}",
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
              "state", "runtime", "memory_mib", "cpus", "disk_gib", "port"}
    if not isinstance(obj, dict) or set(obj) != fields or obj["version"] != 1:
        raise VMError("Unknown VM profile schema")
    if (obj["os"] not in ("windows", "linux") or obj["kind"] not in ("iso", "qcow2")
            or obj["runtime"] not in RUNTIMES or not isinstance(obj["name"], str)
            or not NAME.fullmatch(obj["name"])
            or not isinstance(obj["source_sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", obj["source_sha256"])
            or any(type(obj[k]) is not int for k in ("memory_mib", "cpus", "disk_gib", "port"))
            or not (1024 <= obj["memory_mib"] <= 16384 and 1 <= obj["cpus"] <= 8
                    and 16 <= obj["disk_gib"] <= 256 and 1024 <= obj["port"] <= 65535)):
        raise VMError("Invalid VM profile values")
    if obj["os"] == "windows" and (obj["memory_mib"] < 4096 or obj["cpus"] < 2 or obj["disk_gib"] < 32):
        raise VMError("Windows VM requires at least 4 GiB, 2 vCPUs and 32 GiB disk")
    state = path.parent
    if _path(obj["state"]) != state:
        raise VMError("Profile state must be its own private directory")
    if state.is_symlink() or (stat.S_IMODE(state.stat().st_mode) & 0o077):
        raise VMError("VM state directory must be private")
    if obj["kind"] == "iso":
        source = _path(obj["source"], exists=True)
        if _sha256(source) != obj["source_sha256"]:
            raise VMError("Guest installation ISO changed since creation")
    if obj["kind"] == "qcow2" and not (state / "disk.qcow2").is_file():
        raise VMError("Imported QCOW2 disk missing")
    return obj


def build_image(*, runtime: str, image: str, profile: str = "full") -> dict:
    if image not in ("qemu", "toolbox"):
        raise VMError("Choose image qemu or toolbox")
    if image == "toolbox" and profile not in ("base", "wine", "full"):
        raise VMError("Unknown Linux RE toolbox profile")
    executable = _runtime(runtime)
    dockerfile = ASSETS / ("Dockerfile.qemu" if image == "qemu" else "Dockerfile.re")
    if not dockerfile.is_file():
        raise VMError("VM container assets are missing from installation")
    tag = IMAGES["qemu"] if image == "qemu" else IMAGES[profile]
    argv = [executable, "build", "-f", str(dockerfile), "-t", tag]
    if image == "toolbox":
        argv.extend(["--target", profile])
    argv.append(str(ASSETS))
    result = subprocess.run(argv, check=False, timeout=2400)
    if result.returncode:
        raise VMError(f"{runtime} build failed ({result.returncode})")
    return {"image": tag, "runtime": runtime, "built": True}


def pull_image(*, runtime: str, image: str, profile: str = "full") -> dict:
    """Explicitly fetch prebuilt GHCR image; never auto-pull during VM execution."""
    if image not in ("qemu", "toolbox"):
        raise VMError("Choose image qemu or toolbox")
    if image == "toolbox" and profile not in ("base", "wine", "full"):
        raise VMError("Unsupported RE image profile")
    executable = _runtime(runtime)
    published = ("ghcr.io/rfdetoni/kitt-qemu-vm:" + VERSION if image == "qemu"
                 else "ghcr.io/rfdetoni/kitt-linux-re:" + profile + "-" + VERSION)
    local = IMAGES["qemu"] if image == "qemu" else IMAGES[profile]
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


def _qcow2_validate(binary: str, path: Path) -> None:
    """Inspect QCOW2 inside locally built runner, without opening it as a host drive."""
    _image_ready(binary)
    argv = [
        binary, "run", "--rm", "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--mount", f"type=bind,src={path},dst=/disk.qcow2,readonly",
        "--entrypoint", "qemu-img", IMAGES["qemu"], "info", "--output=json", "/disk.qcow2",
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
               disk_gib: int = 64, port: int = 18006) -> Path:
    if sys.platform != "linux":
        raise VMError("QEMU container backend requires Linux host")
    binary = _runtime(runtime)
    _image_ready(binary)
    if system not in ("windows", "linux") or kind not in ("iso", "qcow2"):
        raise VMError("Unsupported guest format")
    if any(type(x) is not int for x in (memory_mib, cpus, disk_gib, port)):
        raise VMError("CPU/memory/disk/port must be integers")
    if not (1024 <= memory_mib <= 16384 and 1 <= cpus <= 8
            and 16 <= disk_gib <= 256 and 1024 <= port <= 65535):
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
        _qcow2_validate(binary, src)
    with tempfile.TemporaryDirectory(dir=parent, prefix=".kitt-container-") as temp:
        stage = Path(temp)
        if kind == "qcow2":
            shutil.copyfile(src, stage / "disk.qcow2")
            (stage / "disk.qcow2").chmod(0o600)
        name = "kitt-vm-" + hashlib.sha256(str(dest).encode()).hexdigest()[:12]
        profile = {
            "version": 1, "name": name, "os": system, "kind": kind,
            "source": str(src), "source_sha256": _sha256(src),
            "state": str(dest), "runtime": runtime,
            "memory_mib": memory_mib, "cpus": cpus,
            "disk_gib": disk_gib, "port": port,
        }
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
        binary, "run", "--rm", "-d", "--name", profile["name"],
        "--pull", "never",
        "--cap-drop=ALL", "--security-opt=no-new-privileges",
        "--pids-limit", "256", "--memory", f"{profile['memory_mib'] + 1024}m",
        "--cpus", str(profile["cpus"] + 1),
        "--read-only", "--tmpfs", "/tmp:rw,nosuid,size=64m",
        "--network", "bridge",
        "-p", f"127.0.0.1:{profile['port']}:8006",
        "--user", f"{os.getuid()}:{os.getgid()}",
        "--mount", f"type=bind,src={state},dst=/state",
    ]
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
        IMAGES["qemu"],
    ])
    return args


def start_vm(profile_file: Path, *, install: bool = False, allow_tcg: bool = False) -> dict:
    profile = _read_profile(profile_file)
    binary = _runtime(profile["runtime"])
    _image_ready(binary)
    argv = _run_args(profile, binary=binary, install=install, allow_tcg=allow_tcg)
    result = subprocess.run(argv, capture_output=True, timeout=45, check=False)
    if result.returncode:
        raise VMError(f"Container could not start (code={result.returncode}): "
                      + result.stderr.decode("utf-8", "replace")[:300])
    return {"name": profile["name"], "runtime": profile["runtime"],
            "viewer": f"http://127.0.0.1:{profile['port']}/vnc.html",
            "container_id": result.stdout.decode().strip()[:64]}


def operate(profile_file: Path, action: str) -> dict:
    if action not in ("stop", "logs", "status"):
        raise VMError("Unsupported container action")
    profile = _read_profile(profile_file)
    binary = _runtime(profile["runtime"])
    commands = {"stop": ["stop", "-t", "20"], "logs": ["logs", "--tail", "120"],
                "status": ["inspect", "--format", "{{.State.Status}}"]}
    argv = [binary, *commands[action], profile["name"]]
    result = subprocess.run(argv, capture_output=True, timeout=50, check=False)
    if result.returncode:
        if action == "status":
            return {"name": profile["name"], "action": action, "output": "stopped"}
        raise VMError(f"VM container {action} failed (exit {result.returncode})")
    return {"name": profile["name"], "action": action,
            "output": result.stdout.decode("utf-8", "replace")[:16000]}


def doctor(runtime: str) -> dict:
    binary = _runtime(runtime)
    kvm = os.access("/dev/kvm", os.R_OK | os.W_OK)
    image = subprocess.run([binary, "image", "inspect", IMAGES["qemu"]],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
    return {"runtime": runtime, "ready": True, "kvm_available": kvm,
            "qemu_image_built": image.returncode == 0,
            "rootless": runtime == "podman" and os.geteuid() != 0,
            "guest_network_offline": True, "browser_requires_container_bridge": True}


def toolbox(*, runtime: str, profile: str, workspace: Path) -> int:
    if profile not in ("base", "wine", "full"):
        raise VMError("Invalid Linux RE profile")
    binary = _runtime(runtime)
    path = _path(workspace)
    if not path.is_dir() or path.is_symlink():
        raise VMError("Workspace must be an existing directory")
    if not sys.stdin.isatty():
        raise VMError("Toolbox shell requires a real terminal")
    argv = [binary, "run", "--rm", "-it", "--pull", "never",
            "--network", "none", "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "--user", f"{os.getuid()}:{os.getgid()}",
            "--mount", f"type=bind,src={path},dst=/workspace",
            "--workdir", "/workspace", IMAGES[profile], "/bin/sh"]
    if runtime == "podman":
        argv[2:2] = ["--userns=keep-id"]
    return subprocess.run(argv, check=False).returncode
