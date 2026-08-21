from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
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
            if _SECRET_ENV_RE.search(upper) or upper in _DANGEROUS_ENV_NAMES:
                raise PermissionError(f"Refusing secret/injection environment variable: {key}")
            result[key] = value
    return result


class _BoundedCapture:
    def __init__(self, limit: int):
        self.limit = max(1024, int(limit))
        self.data = bytearray()
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
                    remaining = self.limit - len(self.data)
                    if remaining > 0:
                        self.data.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        self.truncated = True
        finally:
            try:
                pipe.close()
            except Exception:
                pass


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

        per_stream_limit = self.max_output_bytes
        out_cap = _BoundedCapture(per_stream_limit)
        err_cap = _BoundedCapture(per_stream_limit)
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

        out = bytes(out_cap.data)
        err = bytes(err_cap.data)
        combined_truncated = out_cap.truncated or err_cap.truncated or (len(out) + len(err) > self.max_output_bytes)
        remaining = self.max_output_bytes
        out = out[:remaining]
        remaining -= len(out)
        err = err[:max(0, remaining)]

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
