"""Operator-owned RE analysis with the active project bind-mounted, not copied.

A fresh networkless container runs the managed Linux RE toolbox *image*.
The active workspace is mounted read-only at /workspace; only a dedicated
analysis output directory is writable. Running kitt-re-full and Windows
oracle containers are never remounted or recreated.
"""
from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath
import re
import secrets
import shutil
import subprocess

from kitt import re_docker_lab
from kitt.security.workspace_fs import WorkspaceFileSystem
from kitt.vm_supervisor import VMError

_IMAGE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_CONTAINER = re.compile(r"[a-f0-9]{64}\Z")
_TIMEOUT = 780


def _project_dir(project: Path) -> Path:
    source = project.expanduser().absolute()
    if not source.is_dir() or source.is_symlink() or source.resolve(strict=True) != source:
        raise VMError("Project workspace must be an existing non-symlink absolute directory")
    if source.stat().st_uid != os.getuid():
        raise VMError("Active project workspace must belong to the current operator")
    if any(mark in str(source) for mark in (",", "\n", "\r")):
        raise VMError("Workspace path is unsuitable for Docker bind mount")
    return source


def _source(root: Path, path: str) -> tuple[str, str, int]:
    if not isinstance(path, str) or not path or "\\" in path or "\x00" in path:
        raise VMError("RE source must be a workspace-relative file")
    candidate, windows = Path(path), PureWindowsPath(path)
    if candidate.is_absolute() or windows.drive or windows.is_absolute() or any(
        item in {"..", ".", ".git", ".kitt"} for item in candidate.parts
    ):
        raise VMError("RE source path is outside the allowed workspace")
    if any(mark in path for mark in (",", "\n", "\r")):
        raise VMError("RE source path is unsuitable for Docker mount")
    try:
        evidence = WorkspaceFileSystem(root).hash_regular(path)
    except (OSError, ValueError, PermissionError) as exc:
        raise VMError(f"Cannot independently fingerprint workspace input: {type(exc).__name__}") from exc
    return evidence.rel_path, evidence.sha256, evidence.size


def _managed_image() -> str:
    guest = re_docker_lab.inspect("linux")
    if not guest or guest.get("managed") is not True or guest.get("status") != "running":
        raise VMError("Managed kitt-re-full is not running or not trusted")
    cid = str(guest.get("container_id", ""))
    if not _CONTAINER.fullmatch(cid):
        raise VMError("Invalid managed Linux RE container ID")
    result = re_docker_lab._docker(
        ["container", "inspect", "--format", "{{.Image}}", cid], timeout=20
    )
    image = result.stdout.decode("ascii", "replace").strip() if result.returncode == 0 else ""
    if not _IMAGE.fullmatch(image):
        raise VMError("Cannot pin trusted Linux RE image SHA256")
    return image


def _output_root(root: Path, *, create: bool) -> Path:
    directory = root
    for part in ("reverse-engineering", "decompiled", "ghidra"):
        directory = directory / part
        if create and not directory.exists() and not directory.is_symlink():
            try:
                directory.mkdir(mode=0o700)
            except OSError as exc:
                raise VMError("Cannot prepare analysis output directory") from exc
        if directory.is_symlink() or (directory.exists() and (
            not directory.is_dir() or directory.stat().st_uid != os.getuid()
        )):
            raise VMError("RE output directory is not a trusted operator directory")
    return directory


def _docker_args(image: str, root: Path, source: str, output: Path,
                 *, job: str) -> list[str]:
    if not _IMAGE.fullmatch(image) or not re.fullmatch(r"[a-f0-9]{12}", job):
        raise VMError("Untrusted image or analysis job identifier")
    # Separate read-only project source from the one writable output location.
    # Docker never sees the host socket, network, credentials or Windows disk.
    return [
        shutil.which("docker") or "docker",
        "run", "--rm", "--pull", "never", "--name", "kitt-re-job-" + job,
        "--network", "none", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--read-only",
        "--pids-limit", "256", "--memory", "4g", "--cpus", "2",
        "--user", f"{os.getuid()}:{os.getgid()}",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=1g,mode=1777",
        "--env", "HOME=/tmp", "--env", "XDG_CACHE_HOME=/tmp/.cache",
        "--env", "XDG_CONFIG_HOME=/tmp/.config",
        "--label", "dev.kitt.re-job=ghidra",
        "--mount", f"type=bind,src={root},dst=/workspace,readonly",
        "--mount", f"type=bind,src={output},dst=/output",
        "--workdir", "/output",
        image, "/opt/ghidra/support/analyzeHeadless",
        "/output", "KittAnalysis", "-import", "/workspace/" + source,
        "-analysisTimeoutPerFile", "600", "-max-cpu", "2",
        "-log", "/output/ghidra-analysis.log",
    ]


def analyze_workspace(*, workspace: Path | None = None, path: str,
                      dry_run: bool = False) -> dict:
    """Share the active workspace with an isolated managed RE image.

    This operator CLI is intentionally not exposed as an unguarded MCP tool.
    A future model-facing operation must go through the host approval gate.
    """
    root = _project_dir(workspace if workspace is not None else Path.cwd())
    source, before_hash, size = _source(root, path)
    image = _managed_image()
    if dry_run:
        output = _output_root(root, create=False) / "<new-run>"
        return {
            "status": "PLAN_ONLY", "workspace": str(root), "source": source,
            "source_size": size, "source_sha256": before_hash,
            "workspace_mount": "read_only", "output": str(output),
            "output_mount": "dedicated_write_only_to_result_dir",
            "linux_toolbox_image": image, "windows_guest_unchanged": True,
            "no_copy": True, "host_execute_requires_operator": True,
        }

    parent = _output_root(root, create=True)
    job = secrets.token_hex(6)
    output = parent / ("job-" + job)
    try:
        output.mkdir(mode=0o700)
    except OSError as exc:
        raise VMError("Cannot allocate private RE output directory") from exc
    argv = _docker_args(image, root, source, output, job=job)
    logfile = output / "docker-analysis.log"
    code = -1
    with logfile.open("wb") as log:
        try:
            proc = subprocess.run(
                argv, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                check=False, timeout=_TIMEOUT,
            )
            code = proc.returncode
        except subprocess.TimeoutExpired as exc:
            # Kill only our uniquely named transient analysis container.
            re_docker_lab._docker(["rm", "--force", "kitt-re-job-" + job], timeout=30)
            raise VMError("Ghidra analysis exceeded bounded host timeout") from exc
        except OSError as exc:
            raise VMError("Cannot run host-owned Docker analysis job") from exc

    _, after_hash, after_size = _source(root, source)
    if before_hash != after_hash or size != after_size:
        raise VMError("Original binary changed during guest RE; results are unverified")
    return {
        "status": "GHIDRA_COMPLETED" if code == 0 else "BLOCKED_ENV",
        "exit_code": code, "source": source, "source_sha256": after_hash,
        "source_size": size, "input_unchanged": True,
        "workspace_shared_read_only": True, "source_copied": False,
        "output": str(output), "log": str(logfile),
        "project_name": "KittAnalysis", "windows_guest_unchanged": True,
        "decompiled_source_exported": False,
    }
