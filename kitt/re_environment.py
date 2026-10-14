"""Operator-owned, opt-in lazy reverse-engineering environment lifecycle.

A normal KITT startup only inspects local configuration: never starts VMs,
pulls images, downloads operating systems, or grants a model host control.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat

from kitt.vm_supervisor import VMError

_CONFIG = Path.home() / ".config" / "kitt" / "re-environments.json"
_SYSTEMS = ("windows", "linux")


def _read_config() -> dict:
    if not _CONFIG.exists():
        return {"version": 1, "profiles": {}}
    if _CONFIG.is_symlink() or not _CONFIG.is_file() or _CONFIG.stat().st_size > 16384:
        raise VMError("Invalid RE environment configuration")
    if os.name == "posix":
        info = _CONFIG.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise VMError("RE environment config must be user-owned and mode 0600")
    try:
        data = json.loads(_CONFIG.read_text("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise VMError("Invalid RE environment configuration JSON") from exc
    if (not isinstance(data, dict) or set(data) != {"version", "profiles"}
            or data["version"] != 1 or not isinstance(data["profiles"], dict)
            or set(data["profiles"]) - set(_SYSTEMS)):
        raise VMError("Unknown RE environment configuration schema")
    for item in data["profiles"].values():
        if (not isinstance(item, dict) or set(item) != {"path", "sha256", "auto"}
                or not isinstance(item["path"], str) or not Path(item["path"]).is_absolute()
                or not isinstance(item["sha256"], str) or len(item["sha256"]) != 64
                or type(item["auto"]) is not bool):
            raise VMError("Invalid enrolled RE environment profile")
    return data


def _write_config(data: dict) -> None:
    directory = _CONFIG.parent
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink():
        raise VMError("Refusing symlink RE config directory")
    import tempfile
    fd, name = tempfile.mkstemp(prefix=".re-", dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(data, output, sort_keys=True, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, _CONFIG)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _profile(path: Path, system: str) -> tuple[Path, dict, str]:
    from kitt.vm_container import _read_profile
    if system not in _SYSTEMS:
        raise VMError("Only Windows or Linux guests can be enrolled")
    target = path.expanduser().absolute()
    if target.is_symlink():
        raise VMError("Refusing symlink VM profile")
    parsed = _read_profile(target)
    if parsed["os"] != system:
        raise VMError("Guest OS does not match selected RE environment")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    return target, parsed, digest


def enroll(system: str, profile: Path, *, auto: bool = False) -> dict:
    """Explicit operator approval of a pinned, installed VM profile."""
    target, parsed, digest = _profile(profile, system)
    if parsed["kind"] == "iso" and not (Path(parsed["state"]) / "disk.qcow2").is_file():
        raise VMError("Install the guest OS before enrolling it")
    config = _read_config()
    config["profiles"][system] = {
        "path": str(target), "sha256": digest, "auto": bool(auto),
    }
    _write_config(config)
    return {"system": system, "profile": str(target), "auto": bool(auto)}


def set_auto(system: str, enabled: bool) -> dict:
    config = _read_config()
    if system not in config["profiles"]:
        raise VMError("Enroll a VM profile first: kitt re enroll <system> <profile>")
    config["profiles"][system]["auto"] = bool(enabled)
    _write_config(config)
    return {"system": system, "auto": bool(enabled)}


def _approved(system: str, *, require_auto: bool = False) -> Path:
    if system not in _SYSTEMS:
        raise VMError("Unknown RE guest type")
    item = _read_config()["profiles"].get(system)
    if not item:
        raise VMError(f"{system} VM missing; run 'kitt re setup {system}' and enroll it")
    if require_auto and not item["auto"]:
        raise VMError(f"{system} auto-start disabled; use 'kitt re auto {system} on'")
    target, _, digest = _profile(Path(item["path"]), system)
    if digest != item["sha256"]:
        raise VMError(f"{system} profile changed; re-enroll explicitly")
    return target


def ensure(system: str, *, automatic: bool = False) -> dict:
    """Start only a trusted guest when actually needed; idempotent if running."""
    from kitt import vm_container
    target = _approved(system, require_auto=automatic)
    current = vm_container.operate(target, "status")
    if current["status"] == "running":
        return {"system": system, "status": "running", "started": False}
    if current["status"] in ("paused", "restarting"):
        raise VMError("Guest is not ready; resolve its state before starting")
    result = vm_container.start_vm(target)
    return {"system": system, "started": True, **result}


def stop(system: str) -> dict:
    from kitt import vm_container
    return vm_container.operate(_approved(system), "stop")


def status() -> dict:
    """Side-effect-free inventory; invalid or missing pieces are reported."""
    from kitt import vm_container
    config = _read_config()
    guests = {}
    for system in _SYSTEMS:
        item = config["profiles"].get(system)
        if item is None:
            guests[system] = {"status": "missing", "setup": f"kitt re setup {system}"}
            continue
        try:
            target = _approved(system)
            current = vm_container.operate(target, "status")
            guests[system] = {**current, "auto": item["auto"], "profile": str(target)}
        except (VMError, OSError) as exc:
            guests[system] = {"status": "blocked", "reason": str(exc), "auto": item["auto"]}
    return {
        "rea": {"installed": shutil.which("rea") is not None, "executable": shutil.which("rea")},
        "runtimes": {name: shutil.which(name) is not None for name in vm_container.RUNTIMES},
        "guests": guests,
        "toolbox": "ephemeral; use kitt vm container toolbox --runtime podman --profile full --workspace PATH",
    }


def ensure_lab_guests() -> list[dict]:
    """Only explicitly opted-in guests are launched for operator-run lab missions."""
    config = _read_config()
    results = []
    for system in _SYSTEMS:
        if config["profiles"].get(system, {}).get("auto"):
            results.append(ensure(system, automatic=True))
    return results


def setup(system: str, *, runtime: str = "podman", source: Path | None = None,
          viewer_port: int | None = None, probe_port: int | None = None) -> dict:
    """Guided install: never fetch a guest OS or execute a silent privileged install."""
    if system == "rea":
        executable = shutil.which("rea")
        return {
            "installed": bool(executable),
            "next": "kitt plugins enable kitt-rea" if executable
                    else "Install REA with KITT root installer --with-rea; then enable kitt-rea",
        }
    if system == "toolbox":
        from kitt.vm_container import _runtime, _image_id, IMAGES
        binary = _runtime(runtime)
        tag = IMAGES["full"]
        try:
            image = _image_id(binary, tag)
            return {"ready": True, "image": image, "next": "kitt vm container toolbox --runtime " + runtime + " --profile full --workspace PATH"}
        except VMError:
            return {"ready": False, "next": "kitt vm container build --runtime " + runtime + " --image toolbox --profile full"}
    if system not in _SYSTEMS:
        raise VMError("Choose windows, linux, rea or toolbox")
    if source is None:
        return {
            "ready": False, "next": f"kitt re setup {system} --source /path/to/{system}.iso --runtime {runtime}",
            "note": "Use your own licensed ISO or standalone QCOW2; installation is interactive",
        }
    from kitt import vm_container
    binary = vm_container._runtime(runtime)
    try:
        vm_container._image_id(binary, vm_container.IMAGES["qemu"])
    except VMError:
        return {"ready": False,
                "next": f"kitt vm container build --runtime {runtime} --image qemu",
                "note": "Build the pinned QEMU image first; retry the guided setup"}
    default_profile = (Path.home() / ".local/share/kitt/vm-containers"
                       / f"{system}-{runtime}" / "profile.json")
    if default_profile.is_file():
        target, _, _ = _profile(default_profile, system)
        return {"ready": True, "profile": str(target),
                "next": f"kitt re enroll {system} {target}",
                "note": "Existing VM kept unchanged; verify the OS is installed before enrolling"}
    from kitt.vm_helper import create_vm
    _, profile = create_vm(
        system=system, source=source, backend=runtime, yes=True,
        port=viewer_port if viewer_port is not None else (18006 if system == "windows" else 18007),
        probe_port=probe_port if probe_port is not None else (18008 if system == "windows" else 18009),
    )
    kind = source.suffix.lower() == ".iso"
    return {
        "created": True, "profile": str(profile),
        "next": f"kitt vm container start --profile {profile}" + (" --install" if kind else ""),
        "enroll": f"kitt re enroll {system} {profile}",
    }
