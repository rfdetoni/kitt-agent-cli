from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from kitt.core.cancellation import CancellationToken


@dataclass(frozen=True)
class ProcessResult:
    argv: List[str]
    returncode: int
    stdout: str
    stderr: str
    duration_ms: float
    timed_out: bool = False
    cancelled: bool = False
    truncated: bool = False
    stdout_total_bytes: int = 0
    stderr_total_bytes: int = 0


_SECRET_ENV_RE = re.compile(
    r"(?i)(^|_)(TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|PRIVATE_?KEY|"
    r"CREDENTIAL|ACCESS_?KEY|AUTH_?TOKEN|REFRESH_?TOKEN)($|_)"
)
_DANGEROUS_ENV_PREFIXES = (
    "DYLD_",
    "GIT_CONFIG_",
)
_DANGEROUS_ENV_NAMES = {
    "BASH_ENV", "ENV", "LD_PRELOAD", "PYTHONSTARTUP",
    "GIT_ASKPASS", "SSH_ASKPASS", "SSH_AUTH_SOCK",
    "AWS_SHARED_CREDENTIALS_FILE", "GOOGLE_APPLICATION_CREDENTIALS",
    "NODE_OPTIONS", "PYTHONPATH",
}


def sanitized_subprocess_env(extra: Optional[dict[str, str]] = None) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if upper in _DANGEROUS_ENV_NAMES:
            continue
        if any(upper.startswith(prefix) for prefix in _DANGEROUS_ENV_PREFIXES):
            continue
        if _SECRET_ENV_RE.search(upper):
            continue
        result[key] = value
    result["GIT_TERMINAL_PROMPT"] = "0"
    result["GIT_OPTIONAL_LOCKS"] = "0"
    result["PAGER"] = "cat"
    result["GIT_PAGER"] = "cat"
    if extra:
        for key, value in extra.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ValueError("Process environment overrides must be strings")
            upper = key.upper()
            if (
                _SECRET_ENV_RE.search(upper)
                or upper in _DANGEROUS_ENV_NAMES
                or any(upper.startswith(prefix) for prefix in _DANGEROUS_ENV_PREFIXES)
            ):
                raise PermissionError(f"Refusing secret/injection environment variable: {key}")
            result[key] = value
    return result


def _trim_head_tail(data: bytes, limit: int) -> bytes:
    """Bound bytes while preserving both diagnostic prefix and final summary."""
    limit = max(0, int(limit))
    if len(data) <= limit:
        return data
    if limit <= 0:
        return b""

    marker = f"\n[KITT capture omitted {len(data) - limit} byte(s)]\n".encode("utf-8")
    if len(marker) >= limit:
        return data[-limit:]

    # Keep more tail than head: test/build summaries and terminal exceptions
    # are disproportionately likely to be at the end of the stream.
    payload = limit - len(marker)
    head_keep = payload // 4
    tail_keep = payload - head_keep
    return data[:head_keep] + marker + data[-tail_keep:]


def _fair_stream_budget(stdout: bytes, stderr: bytes, limit: int) -> tuple[bytes, bytes, bool]:
    """Apply one combined cap without allowing stdout to starve stderr."""
    limit = max(0, int(limit))
    total = len(stdout) + len(stderr)
    if total <= limit:
        return stdout, stderr, False
    if not stdout:
        return b"", _trim_head_tail(stderr, limit), True
    if not stderr:
        return _trim_head_tail(stdout, limit), b"", True

    half = limit // 2
    out_budget = min(len(stdout), half)
    err_budget = min(len(stderr), half)
    remaining = limit - out_budget - err_budget

    # Prefer stderr for leftover capacity because diagnostics commonly live
    # there; give any remainder back to stdout.
    extra_err = min(max(0, len(stderr) - err_budget), remaining)
    err_budget += extra_err
    remaining -= extra_err
    out_budget += min(max(0, len(stdout) - out_budget), remaining)

    return (
        _trim_head_tail(stdout, out_budget),
        _trim_head_tail(stderr, err_budget),
        True,
    )


class _HeadTailCapture:
    """Bounded stream capture retaining both the beginning and the latest bytes."""

    def __init__(self, limit: int):
        self.limit = max(1024, int(limit))
        self.head_limit = max(256, self.limit // 4)
        self.tail_limit = max(0, self.limit - self.head_limit)
        self.head = bytearray()
        self.tail = bytearray()
        self.truncated = False
        self.total_bytes = 0
        self._lock = threading.Lock()

    def consume(self, pipe):
        try:
            while True:
                chunk = pipe.read(64 * 1024)
                if not chunk:
                    break
                with self._lock:
                    self.total_bytes += len(chunk)
                    offset = 0

                    if len(self.head) < self.head_limit:
                        take = min(self.head_limit - len(self.head), len(chunk))
                        self.head.extend(chunk[:take])
                        offset = take

                    if offset < len(chunk) and self.tail_limit > 0:
                        self.tail.extend(chunk[offset:])
                        overflow = len(self.tail) - self.tail_limit
                        if overflow > 0:
                            del self.tail[:overflow]

                    if self.total_bytes > self.limit:
                        self.truncated = True
        finally:
            try:
                pipe.close()
            except Exception:
                pass

    def render(self) -> bytes:
        with self._lock:
            if not self.truncated:
                return bytes(self.head + self.tail)
            omitted = max(0, self.total_bytes - len(self.head) - len(self.tail))
            marker = f"\n[KITT capture omitted {omitted} byte(s)]\n".encode("utf-8")
            tail_budget = max(0, self.tail_limit - len(marker))
            tail = bytes(self.tail[-tail_budget:]) if tail_budget else b""
            return bytes(self.head) + marker + tail


class ProcessRunner:
    def __init__(self, root_dir: str, max_output_bytes: int = 262144):
        self.root = Path(root_dir).resolve()
        self.max_output_bytes = max(4096, int(max_output_bytes))

    @staticmethod
    def _terminate_tree(proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        if os.name != "nt":
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=1.0)
                return
            except Exception:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except Exception:
                    pass
        else:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=sanitized_subprocess_env(),
                    timeout=3,
                    check=False,
                )
            except Exception:
                pass
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=1.0)
        except Exception:
            pass

    def run(
        self,
        argv: List[str],
        timeout_seconds: int = 120,
        cancellation: Optional[CancellationToken] = None,
        env: Optional[dict[str, str]] = None,
    ) -> ProcessResult:
        if not argv or not all(isinstance(x, str) and x for x in argv):
            raise ValueError("argv must be a non-empty string list")
        timeout_seconds = max(1, min(int(timeout_seconds), 3600))
        started = time.monotonic()

        # Each stream gets a full bounded head/tail capture so a noisy stdout
        # cannot erase a short stderr before the final fair combined budget.
        out_cap = _HeadTailCapture(self.max_output_bytes)
        err_cap = _HeadTailCapture(self.max_output_bytes)
        kwargs = dict(
            cwd=self.root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            shell=False,
            close_fds=True,
            restore_signals=True,
            env=sanitized_subprocess_env(env),
        )
        if os.name != "nt":
            kwargs["start_new_session"] = True
        else:
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

        proc = subprocess.Popen(argv, **kwargs)
        out_thread = threading.Thread(target=out_cap.consume, args=(proc.stdout,), daemon=True)
        err_thread = threading.Thread(target=err_cap.consume, args=(proc.stderr,), daemon=True)
        out_thread.start()
        err_thread.start()

        timed_out = False
        cancelled = False
        try:
            while proc.poll() is None:
                if cancellation and cancellation.cancelled:
                    cancelled = True
                    self._terminate_tree(proc)
                    break
                if time.monotonic() - started > timeout_seconds:
                    timed_out = True
                    self._terminate_tree(proc)
                    break
                time.sleep(0.02)
            if proc.poll() is None:
                self._terminate_tree(proc)
            else:
                proc.wait()
        finally:
            out_thread.join(timeout=2.0)
            err_thread.join(timeout=2.0)
            if out_thread.is_alive() or err_thread.is_alive():
                self._terminate_tree(proc)

        raw_out = out_cap.render()
        raw_err = err_cap.render()
        out, err, combined_trimmed = _fair_stream_budget(
            raw_out, raw_err, self.max_output_bytes
        )
        combined_truncated = out_cap.truncated or err_cap.truncated or combined_trimmed

        return ProcessResult(
            list(argv),
            proc.returncode if proc.returncode is not None else -1,
            out.decode("utf-8", "replace"),
            err.decode("utf-8", "replace"),
            (time.monotonic() - started) * 1000,
            timed_out,
            cancelled,
            combined_truncated,
            out_cap.total_bytes,
            err_cap.total_bytes,
        )
