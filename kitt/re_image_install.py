"""Install/verify dedicated RE Docker images without touching running guests.

Installer and operator CLI only. No Docker socket is shared with sandboxed
model tools; the host executes fixed image management commands.
"""
from __future__ import annotations

import shutil
import subprocess

from kitt import vm_container
from kitt.vm_supervisor import VMError

# The Linux toolbox provides REA/Wine; analyzers have independent images.
_IMAGE_SPECS = (
    ("toolbox", "full", "full"),
    ("ghidra", "full", "ghidra"),
    ("frida", "full", "frida"),
)
_PROBES = {
    "full": ["sh", "-ec", "command -v rea; command -v wine; command -v wine-staging"],
    "ghidra": ["sh", "-ec", "test -x /opt/ghidra/support/analyzeHeadless"],
    "frida": ["sh", "-ec", "command -v frida; python -c 'import frida'"],
}


def status(*, runtime: str = "docker") -> dict:
    """Read-only local availability: do not install, pull or start guests."""
    try:
        docker = vm_container._runtime(runtime)
    except (OSError, subprocess.TimeoutExpired, VMError) as exc:
        return {
            "status": "BLOCKED_ENV", "reason": str(exc)[:240],
            "runtime": runtime, "images": {},
            "hint": "Docker Engine must be accessible to the operator, not merely in the agent tool sandbox",
        }
    images = {}
    for _, _, profile in _IMAGE_SPECS:
        tag = vm_container.IMAGES[profile]
        try:
            images[profile] = {
                "status": "READY",
                "image": tag,
                "image_id": vm_container._image_id(docker, tag),
            }
        except (OSError, subprocess.TimeoutExpired, VMError):
            images[profile] = {"status": "MISSING", "image": tag}
    return {
        "status": "READY" if all(x["status"] == "READY" for x in images.values())
        else "MISSING_IMAGES",
        "runtime": runtime, "images": images,
    }


def _smoke(binary: str, image_id: str, profile: str) -> None:
    """Run only a fixed, offline binary-availability check for each image."""
    args = [
        binary, "run", "--rm", "--pull", "never", "--network", "none",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--read-only", "--pids-limit", "128",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m,mode=1777",
        "--env", "HOME=/tmp", image_id, *_PROBES[profile],
    ]
    try:
        result = subprocess.run(args, capture_output=True, timeout=90, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VMError(f"{profile} image smoke check cannot run") from exc
    if result.returncode:
        raise VMError(f"{profile} image smoke check failed ({result.returncode})")


def prepare(*, runtime: str = "docker") -> dict:
    """Ensure all three RE images, preferring registry pull over local build.

    Existing validated local images are reused. No guest containers, disks,
    workspaces, or Windows licenses are changed. Unavailable images fail closed.
    """
    docker = vm_container._runtime(runtime)
    images = {}
    for kind, build_profile, profile in _IMAGE_SPECS:
        tag = vm_container.IMAGES[profile]
        try:
            image_id = vm_container._image_id(docker, tag)
            method = "existing"
        except (OSError, subprocess.TimeoutExpired, VMError):
            try:
                vm_container.pull_image(runtime=runtime, image=kind, profile=build_profile)
                method = "pulled"
            except (OSError, subprocess.TimeoutExpired, VMError):
                # Project-controlled Dockerfile, pinned tool versions and checksums.
                vm_container.build_image(runtime=runtime, image=kind, profile=build_profile)
                method = "built"
            image_id = vm_container._image_id(docker, tag)
        _smoke(docker, image_id, profile)
        images[profile] = {"status": "READY", "image": tag,
                           "image_id": image_id, "source": method}
    return {"status": "READY", "runtime": runtime, "images": images,
            "windows_guest_unchanged": True, "existing_containers_unchanged": True}
