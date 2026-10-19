"""Operator-only host network staging of official Wine-Staging sources.

The managed Linux RE toolbox is networkless; its /workspace is a bind mount
from the operator's private lab workspace. Do not install SSH/GitHub credentials
or enable Docker networking inside the guest.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from kitt import re_docker_lab, re_lab
from kitt.vm_supervisor import VMError

OFFICIAL_WINE_STAGING = "https://github.com/wine-staging/wine-staging.git"
_SHA = re.compile(r"[0-9a-f]{40}\Z")


def stage_wine(*, state_root: Path | None = None, approve: bool = False) -> dict:
    """Stage a fixed upstream repo on the host into the existing shared path."""
    if not approve:
        raise VMError("Operator must approve host network source staging using --approve")
    # This is an explicit local operator action, never a model-facing guest RPC.
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise VMError("Wine source staging requires an interactive operator terminal")
    lab = re_lab._root(state_root)
    linux = re_docker_lab.inspect("linux")
    if linux is None or linux.get("status") != "running":
        raise VMError("The managed Linux RE toolbox must be running")
    workspace = lab / "workspace"
    if (linux.get("workspace") != str(workspace) or workspace.is_symlink()
            or not workspace.is_dir() or workspace.stat().st_uid != os.getuid()):
        raise VMError("Untrusted managed Linux RE workspace")
    if not shutil.which("git"):
        raise VMError("Host git is missing")
    sources = workspace / "sources"
    if sources.is_symlink():
        raise VMError("Refusing symlinked source directory")
    if sources.exists():
        if not sources.is_dir() or sources.stat().st_uid != os.getuid():
            raise VMError("Untrusted source directory")
    else:
        sources.mkdir(mode=0o700)
    dest = sources / "wine-staging"
    if dest.exists() or dest.is_symlink():
        raise VMError("Wine-Staging destination already exists; refusing to overwrite it")
    # Keep the staging directory private and discard partial clones on failure.
    with tempfile.TemporaryDirectory(prefix=".wine-stage-", dir=sources) as tmp:
        target = Path(tmp) / "source"
        # Ignore Git URL rewriting and credential helpers from operator config.
        env = {
            key: value for key, value in os.environ.items()
            if not key.startswith("GIT_")
        }
        env.update({
            "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        })
        try:
            cloned = subprocess.run(
                ["git", "clone", "--depth", "1", "--", OFFICIAL_WINE_STAGING, str(target)],
                check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=300, env=env,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise VMError("Host Wine-Staging clone unavailable or timed out") from exc
        if cloned.returncode:
            raise VMError("Host Wine-Staging clone failed; guest network remains disabled")
        sha = subprocess.run(
            ["git", "-C", str(target), "rev-parse", "HEAD"],
            capture_output=True, timeout=15, check=False, text=True,
        )
        commit = sha.stdout.strip()
        if sha.returncode or not _SHA.fullmatch(commit):
            raise VMError("Cannot verify staged Wine-Staging revision")
        if dest.exists() or dest.is_symlink():
            raise VMError("Wine-Staging destination appeared during clone")
        target.rename(dest)
    return {
        "status": "STAGED", "source": OFFICIAL_WINE_STAGING,
        "commit": commit, "guest_path": "/workspace/sources/wine-staging",
        "guest_network_enabled": False,
    }
