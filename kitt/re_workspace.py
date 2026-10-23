"""Operator-owned RE analysis with the active project bind-mounted, not copied.

A fresh networkless container runs the managed Linux RE toolbox *image*.
The active workspace is mounted read-only at /workspace; only a dedicated
analysis output directory is writable. Running kitt-re-full and Windows
oracle containers are never remounted or recreated.
"""
from __future__ import annotations

import json
import os
import contextlib
import fcntl
import stat
from pathlib import Path, PureWindowsPath
import re
import secrets
import shutil
import signal
import threading
import subprocess
import sys
import time

from kitt import re_docker_lab
from kitt.security.workspace_fs import WorkspaceFileSystem
from kitt.vm_supervisor import VMError

_IMAGE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_CONTAINER = re.compile(r"[a-f0-9]{64}\Z")
_PROGRESS_INTERVAL_SECONDS = 15
_STALL_WARNING_SECONDS = 600
_DEFAULT_MEMORY_MIB = 4096
_DEFAULT_HEAP_MIB = 2048
_DEFAULT_CPUS = 2


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
                 ghidra_log: str = "ghidra-analysis.log",
                 analysis_timeout_seconds: int | None = None,
                 memory_mib: int = _DEFAULT_MEMORY_MIB,
                 heap_mib: int = _DEFAULT_HEAP_MIB,
                 cpus: int = _DEFAULT_CPUS) -> list[str]:
    if not _IMAGE.fullmatch(image) or not re.fullmatch(r"[a-f0-9]{12}", job):
        raise VMError("Untrusted image or analysis job identifier")
    if not 768 <= memory_mib <= 131072 or not 256 <= heap_mib <= memory_mib - 512:
        raise VMError("Ghidra heap must leave >=512 MiB of container memory for native overhead")
    if not 1 <= cpus <= 64:
        raise VMError("Ghidra CPU limit must be between 1 and 64")
    if analysis_timeout_seconds is not None and analysis_timeout_seconds <= 0:
        raise VMError("Ghidra per-file timeout must be positive")
    # No --rm: inspect the stopped container's OOMKilled flag before removing
    # ONLY this transient job in finally. No network or Docker socket in guest.
    return [
        shutil.which("docker") or "docker",
        "run", "--pull", "never", "--name", "kitt-re-job-" + job,
        "--network", "none", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--read-only",
        "--pids-limit", "256", "--memory", f"{memory_mib}m", "--cpus", str(cpus),
        "--user", f"{os.getuid()}:{os.getgid()}",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=1g,mode=1777",
        "--env", "HOME=/tmp", "--env", "XDG_CACHE_HOME=/tmp/.cache",
        "--env", "XDG_CONFIG_HOME=/tmp/.config",
        "--env", f"GHIDRA_HEADLESS_MAXMEM={heap_mib}M",
        "--label", "dev.kitt.re-job=ghidra",
        "--mount", f"type=bind,src={root},dst=/workspace,readonly",
        "--mount", f"type=bind,src={output},dst=/output",
        "--workdir", "/output",
        image, "/opt/ghidra/support/analyzeHeadless",
        "/output", "KittAnalysis",
        *(["-process", Path(source).name] if resume else ["-import", "/workspace/" + source]),
        *([] if analysis_timeout_seconds is None
          else ["-analysisTimeoutPerFile", str(analysis_timeout_seconds)]),
        "-max-cpu", str(cpus),
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


def _log_evidence(logfile: Path) -> dict[str, bool]:
    """Scan once in constant RAM. Exit 0 and import/save alone prove no analysis success."""
    evidence = {"analysis_succeeded": False, "saved": False,
                "timed_out": False, "incomplete": False, "imported": False}
    with logfile.open("rb") as log:
        for line in log:
            low = line.lower()
            if b"report: analysis succeeded" in low:
                evidence["analysis_succeeded"] = True
            if b"report: save succeeded" in low:
                evidence["saved"] = True
            if b"analysis timed out" in low:
                evidence["timed_out"] = True
            if b"processing not completed" in low:
                evidence["incomplete"] = True
            if b"report: import succeeded" in low:
                evidence["imported"] = True
    return evidence


def _analysis_timed_out(logfile: Path) -> bool:
    evidence = _log_evidence(logfile)
    return evidence["timed_out"] and evidence["incomplete"]


def _sample_stats(container: str) -> dict | None:
    """One bounded Docker snapshot per heartbeat; never a polling sidecar."""
    try:
        result = re_docker_lab._docker(
            ["stats", "--no-stream", "--format", "{{json .}}", container], timeout=6)
        if result.returncode != 0:
            return None
        rows = result.stdout.decode("utf-8", "replace").splitlines()
        data = json.loads(rows[-1]) if rows else None
        return data if isinstance(data, dict) else None
    except (VMError, OSError, ValueError, AttributeError, IndexError):
        return None


def _inspect_oom(container: str) -> bool:
    try:
        result = re_docker_lab._docker(
            ["container", "inspect", "--format", "{{json .State}}", container], timeout=10)
        state = json.loads(result.stdout) if result.returncode == 0 else {}
        return state.get("OOMKilled") is True if isinstance(state, dict) else False
    except (VMError, OSError, ValueError, AttributeError):
        return False


def _remove_job(container: str) -> bool:
    try:
        result = re_docker_lab._docker(["rm", "--force", container], timeout=20)
        return result.returncode == 0
    except (VMError, OSError, AttributeError):
        return False


@contextlib.contextmanager
def _cancel_on_sigterm():
    """Treat operator SIGTERM like Ctrl+C so the transient Docker job is cleaned."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.getsignal(signal.SIGTERM)

    def cancel(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, cancel)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


@contextlib.contextmanager
def _project_lock(output: Path):
    """Do not let two resumptions write concurrently to the same Ghidra DB."""
    flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        fd = os.open(output / ".kitt-re-analysis.lock", flags, 0o600)
    except OSError as exc:
        raise VMError("Cannot securely lock Ghidra project") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise VMError("Untrusted Ghidra project lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise VMError("Ghidra project is already being analyzed by another job") from exc
        yield
    finally:
        os.close(fd)


class _ProgressMonitor:
    """Best-effort *activity* indicators, never fabricated completion percentages."""

    def __init__(self, logfile: Path, container: str, started: float,
                 stall_warning_seconds: int = _STALL_WARNING_SECONDS):
        self.logfile = logfile
        self.container = container
        self.started = started
        self.last_signal = started
        self.stall_warning_seconds = stall_warning_seconds
        self.last_size = 0
        self.phase = "STARTING"
        self.last_io = None
        self.telemetry_available = False
        self.state = "STARTING"

    def report(self) -> None:
        now = time.monotonic()
        try:
            size = self.logfile.stat().st_size
            if size < self.last_size:
                self.last_size = 0
            with self.logfile.open("rb") as log:
                log.seek(max(self.last_size, size - 65536))
                chunk = log.read(65536).decode("utf-8", "replace")
            delta = max(0, size - self.last_size)
            self.last_size = size
        except OSError:
            delta, chunk = 0, ""
        if delta:
            self.last_signal = now
        for line in chunk.splitlines():
            if "IMPORTING:" in line:
                self.phase = "IMPORTING"
            elif "ANALYZING" in line or "Analysis Task" in line:
                self.phase = "ANALYZING"
            elif "Save succeeded" in line or "SAVING" in line:
                self.phase = "SAVING"
        stats = _sample_stats(self.container)
        cpu = memory = io_display = "unavailable"
        self.telemetry_available = stats is not None
        if stats is not None:
            cpu = str(stats.get("CPUPerc", "unavailable"))
            memory = str(stats.get("MemUsage", "unavailable"))
            io_display = str(stats.get("BlockIO", "unavailable"))
            try:
                cpu_active = float(cpu.strip().rstrip("%")) > 0.5
            except ValueError:
                cpu_active = False
            io = stats.get("BlockIO")
            io_active = bool(io and self.last_io is not None and io != self.last_io)
            if io:
                self.last_io = io
            if cpu_active or io_active:
                self.last_signal = now
        silent = now - self.last_signal
        self.state = ("POSSIBLY_STALLED"
                      if silent >= self.stall_warning_seconds and self.telemetry_available
                      else "RUNNING_NO_RECENT_SIGNAL" if silent >= self.stall_warning_seconds
                      else self.phase)
        print(
            f"[KITT RE] {self.container} | {self.state} | {int(now-self.started)}s | "
            f"CPU {cpu} | RAM {memory} | I/O {io_display} | log +{delta} bytes | "
            f"telemetry {'available' if self.telemetry_available else 'unavailable'} | "
            f"{self.logfile}", file=sys.stderr, flush=True,
        )


def _report_progress(stop, logfile: Path, started: float) -> None:
    """Legacy heartbeat helper retained for callers; production uses _ProgressMonitor."""
    while not stop.wait(_PROGRESS_INTERVAL_SECONDS):
        try:
            size = logfile.stat().st_size
        except OSError:
            size = 0
        print(f"[KITT RE] Ghidra em execução há {int(time.monotonic()-started)}s; "
              f"log Docker: {logfile} ({size} bytes).",
              file=sys.stderr, flush=True)


def _positive_optional(name: str, value: int | None) -> None:
    if value is not None and (not isinstance(value, int) or value <= 0):
        raise VMError(f"{name} must be a positive number of seconds")


def analyze_workspace(*, workspace: Path | None = None, path: str,
                      dry_run: bool = False, resume_job: str | None = None,
                      host_timeout_seconds: int | None = None,
                      analysis_timeout_seconds: int | None = None,
                      stall_warning_seconds: int = _STALL_WARNING_SECONDS,
                      memory_mib: int = _DEFAULT_MEMORY_MIB,
                      heap_mib: int = _DEFAULT_HEAP_MIB,
                      cpus: int = _DEFAULT_CPUS) -> dict:
    """Run one isolated Ghidra job. Time limits are opt-in, never implicit.

    Operator-only CLI. Model-facing execution must still pass host authorization.
    """
    _positive_optional("host timeout", host_timeout_seconds)
    _positive_optional("analysis timeout", analysis_timeout_seconds)
    _positive_optional("stall warning", stall_warning_seconds)
    if not isinstance(memory_mib, int) or not isinstance(heap_mib, int) or not isinstance(cpus, int):
        raise VMError("Resource limits must be integers")
    if not 768 <= memory_mib <= 131072 or not 256 <= heap_mib <= memory_mib - 512:
        raise VMError("Ghidra heap requires >=512 MiB of non-heap headroom")
    if not 1 <= cpus <= 64:
        raise VMError("Ghidra CPUs must be between 1 and 64")
    root = _project_dir(workspace if workspace is not None else Path.cwd())
    source, before_hash, size = _source(root, path)
    image = _managed_image()
    resources = {"memory_mib": memory_mib, "heap_mib": heap_mib, "cpus": cpus}
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
            "analysis_timeout_seconds": analysis_timeout_seconds,
            "host_timeout_seconds": host_timeout_seconds,
            "stall_warning_seconds": stall_warning_seconds, "resource_limits": resources,
        }

    parent = _output_root(root, create=True)
    job = secrets.token_hex(6)
    container = "kitt-re-job-" + job
    if resume_job:
        output = _resume_output(root, source, before_hash, resume_job)
        # New logs for every resume; do not replace the existing project or evidence.
        suffix = "resume-" + job
        ghidra_log = suffix + "-ghidra-analysis.log"
        logfile = output / (suffix + "-docker-analysis.log")
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
        analysis_timeout_seconds=analysis_timeout_seconds,
        memory_mib=memory_mib, heap_mib=heap_mib, cpus=cpus,
    )
    print(
        f"[KITT RE] {'Retomando projeto salvo' if resume_job else 'Iniciando Ghidra'} "
        f"para {source} ({size} bytes). Projeto: {output}. Log: {logfile}",
        file=sys.stderr, flush=True,
    )
    print(
        f"[KITT RE] Timeout total: {host_timeout_seconds or 'desativado'}; "
        f"timeout Ghidra: {analysis_timeout_seconds or 'desativado'}. "
        f"Heartbeat: {_PROGRESS_INTERVAL_SECONDS}s. Ctrl+C cancela apenas {container}.",
        file=sys.stderr, flush=True,
    )

    started = time.monotonic()
    monitor = _ProgressMonitor(logfile, container, started, stall_warning_seconds)
    proc = None
    code = -1
    cancelled = False
    host_timed_out = False
    oom = False
    cleanup_ok = True
    # fcntl lock is operator-owned and safe across independent CLI processes.
    with _project_lock(output), _cancel_on_sigterm():
        with logfile.open("wb") as log:
            try:
                proc = subprocess.Popen(
                    argv, stdin=subprocess.DEVNULL,
                    stdout=log, stderr=subprocess.STDOUT,
                )
                while True:
                    remaining = (None if host_timeout_seconds is None else
                                 host_timeout_seconds - (time.monotonic() - started))
                    if remaining is not None and remaining <= 0:
                        host_timed_out = True
                        break
                    interval = (_PROGRESS_INTERVAL_SECONDS if remaining is None else
                                min(_PROGRESS_INTERVAL_SECONDS, remaining))
                    try:
                        code = proc.wait(timeout=interval)
                        break
                    except subprocess.TimeoutExpired:
                        monitor.report()
            except KeyboardInterrupt:
                cancelled = True
            except OSError as exc:
                raise VMError(f"Cannot start/monitor Ghidra Docker job; log: {logfile}") from exc
            finally:
                # Docker --rm is intentionally absent; inspect OOM before removal.
                if proc is not None and proc.poll() is not None:
                    oom = _inspect_oom(container)
                cleanup_ok = _remove_job(container)
                if proc is not None and proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5)

    if not cleanup_ok:
        raise VMError(f"Could not remove transient Ghidra container {container}; inspect Docker")
    _, after_hash, after_size = _source(root, source)
    if before_hash != after_hash or size != after_size:
        raise VMError("Original binary changed during guest RE; results are unverified")
    evidence = _log_evidence(logfile)
    timed_out = evidence["timed_out"] and evidence["incomplete"]
    if cancelled:
        status = "CANCELLED"
    elif host_timed_out:
        status = "PARTIAL"
    elif oom:
        status = "OOM"
    elif code != 0:
        status = "FAILED"
    elif timed_out:
        status = "ANALYSIS_TIMED_OUT"
    elif evidence["analysis_succeeded"] and evidence["saved"]:
        status = "GHIDRA_COMPLETED"
    else:
        status = "PARTIAL"
    print(
        f"[KITT RE] {container} finalizado: {status}; exit_code={code}; "
        f"log: {logfile}", file=sys.stderr, flush=True,
    )
    return {
        "status": status, "analysis_timed_out": timed_out,
        "analysis_success_reported": evidence["analysis_succeeded"],
        "save_reported": evidence["saved"],
        "completion_scope": ("ghidra_report_only" if status == "GHIDRA_COMPLETED" else None),
        "cancelled": cancelled, "host_timed_out": host_timed_out, "oom_killed": oom,
        "mode": "resume" if resume_job else "import",
        "resume_job": resume_job or output.name,
        "analysis_timeout_seconds": analysis_timeout_seconds,
        "host_timeout_seconds": host_timeout_seconds,
        "stall_warning_seconds": stall_warning_seconds,
        "resource_limits": resources,
        "last_monitor_state": monitor.state,
        "telemetry_available": monitor.telemetry_available,
        "exit_code": code, "source": source, "source_sha256": after_hash,
        "source_size": size, "input_unchanged": True,
        "workspace_shared_read_only": True, "source_copied": False,
        "output": str(output), "log": str(logfile),
        "ghidra_log": str(output / ghidra_log),
        "project_name": "KittAnalysis", "windows_guest_unchanged": True,
        "decompiled_source_exported": False,
    }
