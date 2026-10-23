"""Operator-owned RE analysis with the active project bind-mounted, not copied.

A fresh networkless container runs the managed Linux RE toolbox *image*.
The active workspace is mounted read-only at /workspace; only a dedicated
analysis output directory is writable. Running kitt-re-full and Windows
oracle containers are never remounted or recreated.
"""
from __future__ import annotations

import json
import os
from pathlib import Path, PureWindowsPath
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time

from kitt import re_docker_lab
from kitt.security.workspace_fs import WorkspaceFileSystem
from kitt.vm_supervisor import VMError

_IMAGE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_CONTAINER = re.compile(r"[a-f0-9]{64}\Z")
_TIMEOUT = 780
_PROGRESS_INTERVAL_SECONDS = 15
_RESUME_ANALYSIS_SECONDS = 1200
_RESUME_HOST_TIMEOUT = 1380


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
                 *, job: str, resume: bool = False,
                 ghidra_log: str = "ghidra-analysis.log") -> list[str]:
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
        "/output", "KittAnalysis",
        *(["-process", Path(source).name] if resume else ["-import", "/workspace/" + source]),
        "-analysisTimeoutPerFile", str(_RESUME_ANALYSIS_SECONDS if resume else 600),
        "-max-cpu", "2",
        "-log", "/output/" + ghidra_log,
    ]


def _resume_output(root: Path, source: str, checksum: str, job_name: str) -> Path:
    """Validate operator-selected saved project; never import over existing data."""
    if not re.fullmatch(r"job-[0-9a-f]{12}", job_name):
        raise VMError("Invalid RE resume job name")
    output = _output_root(root, create=False) / job_name
    if output.is_symlink() or not output.is_dir() or output.stat().st_uid != os.getuid():
        raise VMError("Saved Ghidra job directory is missing or not operator-owned")
    for child in ("KittAnalysis.gpr", "KittAnalysis.rep"):
        entry = output / child
        if entry.is_symlink() or not entry.exists() or entry.stat().st_uid != os.getuid():
            raise VMError("Existing Ghidra project is missing or untrusted")
    if not (output / "KittAnalysis.gpr").is_file() or not (output / "KittAnalysis.rep").is_dir():
        raise VMError("Existing Ghidra project has invalid structure")

    evidence = output / "kitt-re-input.json"
    if evidence.is_symlink():
        raise VMError("Untrusted RE input manifest")
    if evidence.is_file():
        try:
            recorded = json.loads(evidence.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise VMError("Invalid RE input manifest") from exc
        if recorded.get("source") != source or recorded.get("sha256") != checksum:
            raise VMError("Saved Ghidra project does not match source SHA256 and path")
    else:
        # 0.88.73/74 jobs did not persist a fingerprint manifest; accept only
        # an operator-selected job with an independently saved import receipt.
        log = output / "docker-analysis.log"
        if log.is_symlink() or not log.is_file() or log.stat().st_size > 4 * 1024 * 1024:
            raise VMError("Legacy Ghidra import receipt missing or oversized")
        content = log.read_text(encoding="utf-8", errors="replace")
        if (f"IMPORTING: file:///workspace/{source}" not in content or
                f"Save succeeded for: /{Path(source).name}" not in content):
            raise VMError("Legacy job lacks an import/save receipt for this source")
    return output


def _analysis_timed_out(logfile: Path) -> bool:
    """Ghidra may return process success after its per-file analysis timed out."""
    with logfile.open("rb") as source:
        size = source.seek(0, 2)
        source.seek(max(0, size - 512 * 1024))
        tail = source.read().lower()
    return b"analysis timed out" in tail and b"processing not completed" in tail


def _report_progress(stop: threading.Event, logfile: Path, started: float) -> None:
    """Show a bounded heartbeat while Docker/Ghidra output goes to a log file."""
    while not stop.wait(_PROGRESS_INTERVAL_SECONDS):
        try:
            bytes_logged = logfile.stat().st_size
        except OSError:
            bytes_logged = 0
        elapsed = int(time.monotonic() - started)
        print(
            f"[KITT RE] Ghidra em execução há {elapsed}s; "
            f"log Docker: {logfile} ({bytes_logged} bytes). "
            "A análise pode demorar vários minutos.",
            file=sys.stderr, flush=True,
        )


def analyze_workspace(*, workspace: Path | None = None, path: str,
                      dry_run: bool = False, resume_job: str | None = None) -> dict:
    """Share the active workspace with an isolated managed RE image.

    This operator CLI is intentionally not exposed as an unguarded MCP tool.
    A future model-facing operation must go through the host approval gate.
    """
    root = _project_dir(workspace if workspace is not None else Path.cwd())
    source, before_hash, size = _source(root, path)
    image = _managed_image()
    if dry_run:
        output = (_resume_output(root, source, before_hash, resume_job)
                  if resume_job else _output_root(root, create=False) / "<new-run>")
        return {
            "status": "PLAN_ONLY", "workspace": str(root), "source": source,
            "source_size": size, "source_sha256": before_hash,
            "workspace_mount": "read_only", "output": str(output),
            "output_mount": "dedicated_write_only_to_result_dir",
            "linux_toolbox_image": image, "windows_guest_unchanged": True,
            "no_copy": True, "host_execute_requires_operator": True,
            "mode": "resume" if resume_job else "import",
            "analysis_timeout_seconds": _RESUME_ANALYSIS_SECONDS if resume_job else 600,
        }

    parent = _output_root(root, create=True)
    job = secrets.token_hex(6)
    if resume_job:
        output = _resume_output(root, source, before_hash, resume_job)
        # Keep original logs and the persisted Ghidra project intact.
        run_suffix = "resume-" + job
        ghidra_log = run_suffix + "-ghidra-analysis.log"
        logfile = output / (run_suffix + "-docker-analysis.log")
    else:
        output = parent / ("job-" + job)
        try:
            output.mkdir(mode=0o700)
            (output / "kitt-re-input.json").write_text(
                json.dumps({"source": source, "sha256": before_hash, "size": size}) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            raise VMError("Cannot allocate private RE output directory") from exc
        ghidra_log = "ghidra-analysis.log"
        logfile = output / "docker-analysis.log"
    argv = _docker_args(
        image, root, source, output, job=job,
        resume=bool(resume_job), ghidra_log=ghidra_log,
    )
    host_timeout = _RESUME_HOST_TIMEOUT if resume_job else _TIMEOUT
    code = -1
    print(
        f"[KITT RE] {'Retomando projeto salvo' if resume_job else 'Iniciando Ghidra'} "
        f"para {source} ({size} bytes). "
        f"Projeto: {output}. Log: {logfile}",
        file=sys.stderr, flush=True,
    )
    print(
        f"[KITT RE] Execução síncrona; limite de {host_timeout}s no host. "
        f"Progresso exibido a cada {_PROGRESS_INTERVAL_SECONDS}s. "
        "A saída JSON será emitida quando terminar.",
        file=sys.stderr, flush=True,
    )
    with logfile.open("wb") as log:
        stopped = threading.Event()
        progress = threading.Thread(
            target=_report_progress,
            args=(stopped, logfile, time.monotonic()),
            daemon=True,
        )
        progress.start()
        try:
            proc = subprocess.run(
                argv, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                check=False, timeout=host_timeout,
            )
            code = proc.returncode
        except subprocess.TimeoutExpired as exc:
            # Kill only our uniquely named transient analysis container.
            re_docker_lab._docker(["rm", "--force", "kitt-re-job-" + job], timeout=30)
            raise VMError(f"Ghidra excedeu o limite de {host_timeout}s; log: {logfile}") from exc
        except KeyboardInterrupt as exc:
            # Interrupting Docker's CLI alone might leave its container running.
            re_docker_lab._docker(["rm", "--force", "kitt-re-job-" + job], timeout=30)
            raise VMError(f"Análise Ghidra cancelada; log preservado em {logfile}") from exc
        except OSError as exc:
            raise VMError(f"Falha ao iniciar contêiner Ghidra; log: {logfile}") from exc
        finally:
            stopped.set()
            progress.join(timeout=1)

    print(
        f"[KITT RE] Ghidra finalizou com código {code}. "
        f"Verifique {logfile}",
        file=sys.stderr, flush=True,
    )
    _, after_hash, after_size = _source(root, source)
    if before_hash != after_hash or size != after_size:
        raise VMError("Original binary changed during guest RE; results are unverified")
    analysis_timed_out = _analysis_timed_out(logfile)
    status = ("ANALYSIS_TIMED_OUT" if analysis_timed_out else
              "GHIDRA_COMPLETED" if code == 0 else "BLOCKED_ENV")
    return {
        "status": status,
        "analysis_timed_out": analysis_timed_out,
        "mode": "resume" if resume_job else "import",
        "resume_job": resume_job or output.name,
        "analysis_timeout_seconds": _RESUME_ANALYSIS_SECONDS if resume_job else 600,
        "exit_code": code, "source": source, "source_sha256": after_hash,
        "source_size": size, "input_unchanged": True,
        "workspace_shared_read_only": True, "source_copied": False,
        "output": str(output), "log": str(logfile),
        "ghidra_log": str(output / ghidra_log),
        "project_name": "KittAnalysis", "windows_guest_unchanged": True,
        "decompiled_source_exported": False,
    }
