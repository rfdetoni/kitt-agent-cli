"""Process-isolated facade for K.I.T.T.'s safe Python compute tool."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


PYTHON_TOOL_CALL_OPEN = "<kitt-python-compute>"
PYTHON_TOOL_CALL_CLOSE = "</kitt-python-compute>"


def parse_python_compute_call(response: str) -> Optional[Dict[str, Any]]:
    """Parse an exact, text-provider-compatible safe Python tool request.

    The wrapper must be the complete assistant response.  This prevents prose,
    quoted examples, or user-controlled text from being mistaken for a call.
    """
    stripped = response.strip()
    if not stripped.startswith(PYTHON_TOOL_CALL_OPEN):
        return None
    if not stripped.endswith(PYTHON_TOOL_CALL_CLOSE):
        raise ValueError("Incomplete python_compute tool-call wrapper.")
    body = stripped[len(PYTHON_TOOL_CALL_OPEN) : -len(PYTHON_TOOL_CALL_CLOSE)].strip()
    try:
        args = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid python_compute JSON: {exc.msg}") from exc
    if not isinstance(args, dict):
        raise ValueError("python_compute payload must be a JSON object.")
    unknown = set(args) - {"code", "inputs", "result_var"}
    if unknown:
        raise ValueError(f"Unknown python_compute arguments: {', '.join(sorted(unknown))}.")
    if not isinstance(args.get("code"), str):
        raise ValueError("python_compute code must be a string.")
    if "inputs" in args and not isinstance(args["inputs"], dict):
        raise ValueError("python_compute inputs must be a JSON object.")
    if "result_var" in args and not isinstance(args["result_var"], str):
        raise ValueError("python_compute result_var must be a string.")
    return args


@dataclass(frozen=True)
class SafePythonConfig:
    timeout_seconds: float = 3.0
    cpu_seconds: int = 2
    memory_bytes: int = 256 * 1024 * 1024
    max_code_bytes: int = 16 * 1024
    max_input_bytes: int = 64 * 1024
    max_output_bytes: int = 64 * 1024
    max_steps: int = 50_000
    max_ast_nodes: int = 4_000
    max_collection_items: int = 10_000
    max_output_chars: int = 32_768
    max_value_bytes: int = 8 * 1024 * 1024


@dataclass(frozen=True)
class SafePythonExecution:
    success: bool
    output: str
    error: Optional[str]
    truncated: bool
    duration_ms: float
    steps: int = 0


class SafePythonExecutor:
    """Run the AST interpreter in an isolated, resource-limited process."""

    def __init__(self, config: Optional[SafePythonConfig] = None):
        self.config = config or SafePythonConfig()
        self.worker_path = Path(__file__).with_name("safe_python_worker.py").resolve()

    def execute(self, code: str, inputs: Optional[Dict[str, Any]] = None, result_var: str = "_result") -> SafePythonExecution:
        started = time.monotonic()
        if not isinstance(code, str) or not code.strip():
            return self._failure("code must be a non-empty string.", started)
        if len(code.encode("utf-8")) > self.config.max_code_bytes:
            return self._failure(f"code exceeds {self.config.max_code_bytes} bytes.", started)
        if not isinstance(inputs or {}, dict):
            return self._failure("inputs must be a JSON object.", started)

        request = {
            "code": code,
            "inputs": inputs or {},
            "result_var": result_var,
            "limits": {
                "max_steps": self.config.max_steps,
                "max_ast_nodes": self.config.max_ast_nodes,
                "max_collection_items": self.config.max_collection_items,
                "max_output_chars": self.config.max_output_chars,
                "max_value_bytes": self.config.max_value_bytes,
                "timeout_seconds": min(self.config.timeout_seconds, float(self.config.cpu_seconds) + 1.0),
            },
        }
        try:
            payload = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError) as exc:
            return self._failure(f"inputs must contain JSON-compatible values: {exc}", started)
        if len(payload) > self.config.max_input_bytes:
            return self._failure(f"request exceeds {self.config.max_input_bytes} bytes.", started)

        environment = {
            "PATH": os.defpath,
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }

        with tempfile.TemporaryDirectory(prefix="kitt-safe-python-") as temp_dir:
            with tempfile.TemporaryFile() as stdin_file, tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
                stdin_file.write(payload)
                stdin_file.seek(0)
                try:
                    process = subprocess.Popen(
                        [sys.executable, "-I", "-S", str(self.worker_path)],
                        stdin=stdin_file,
                        stdout=stdout_file,
                        stderr=stderr_file,
                        cwd=temp_dir,
                        env=environment,
                        shell=False,
                        start_new_session=True,
                        preexec_fn=self._resource_limiter() if os.name == "posix" else None,
                    )
                    try:
                        process.wait(timeout=self.config.timeout_seconds)
                    except subprocess.TimeoutExpired:
                        self._terminate(process)
                        return self._failure("Safe Python execution timed out.", started)
                except OSError as exc:
                    return self._failure(f"Could not start isolated Python worker: {exc}", started)

                stdout_file.seek(0)
                raw_output = stdout_file.read(self.config.max_output_bytes + 1)
                truncated = len(raw_output) > self.config.max_output_bytes
                raw_output = raw_output[: self.config.max_output_bytes]
                stderr_file.seek(0)
                raw_error = stderr_file.read(4097)[:4096]

        try:
            response = json.loads(raw_output.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            detail = raw_error.decode("utf-8", errors="replace").strip()
            return self._failure(
                f"Safe Python worker returned an invalid response{': ' + detail if detail else '.'}",
                started,
                truncated=truncated,
            )

        duration_ms = (time.monotonic() - started) * 1000.0
        if not response.get("success"):
            return SafePythonExecution(
                success=False,
                output="",
                error=str(response.get("error", "Safe Python execution failed.")),
                truncated=truncated,
                duration_ms=duration_ms,
                steps=int(response.get("steps", 0)),
            )

        rendered = json.dumps(
            {
                "stdout": response.get("stdout", ""),
                "result": response.get("result"),
                "steps": int(response.get("steps", 0)),
            },
            ensure_ascii=False,
            indent=2,
        )
        return SafePythonExecution(
            success=True,
            output=rendered,
            error=None,
            truncated=bool(response.get("truncated")) or truncated,
            duration_ms=duration_ms,
            steps=int(response.get("steps", 0)),
        )

    def _resource_limiter(self):
        config = self.config

        def apply_limits() -> None:
            try:
                import resource

                resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
                resource.setrlimit(resource.RLIMIT_CPU, (config.cpu_seconds, config.cpu_seconds + 1))
                resource.setrlimit(resource.RLIMIT_AS, (config.memory_bytes, config.memory_bytes))
                resource.setrlimit(resource.RLIMIT_FSIZE, (config.max_output_bytes * 2, config.max_output_bytes * 2))
                resource.setrlimit(resource.RLIMIT_NOFILE, (16, 16))
            except (ImportError, OSError, ValueError):
                # The AST interpreter remains the security boundary; OS limits are defense in depth.
                pass

        return apply_limits

    @staticmethod
    def _terminate(process: subprocess.Popen) -> None:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
        process.wait(timeout=1)

    @staticmethod
    def _failure(error: str, started: float, truncated: bool = False) -> SafePythonExecution:
        return SafePythonExecution(
            success=False,
            output="",
            error=error,
            truncated=truncated,
            duration_ms=(time.monotonic() - started) * 1000.0,
        )
