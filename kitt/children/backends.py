from __future__ import annotations

import os
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from kitt.core.cancellation import CancellationToken
from kitt.tools.process_runner import ProcessRunner


_TRUE = {"1", "true", "yes", "on", "enabled"}


@dataclass(frozen=True)
class ChildAgentBackendStatus:
    name: str
    executable: str | None
    available: bool
    enabled: bool
    detail: str = ""


@dataclass(frozen=True)
class ExternalBackendResult:
    backend: str
    success: bool
    output: str
    error: str | None = None
    returncode: int = 0
    duration_ms: float = 0.0
    timed_out: bool = False
    cancelled: bool = False


@dataclass(frozen=True)
class ChildAgentBackend:
    """One explicitly selected external coding-agent CLI.

    Commands are argv templates, never shell strings. ``{task}`` is replaced in
    exactly one argument; if omitted the task is appended as the final argument.
    """

    name: str
    executables: tuple[str, ...]
    argv: tuple[str, ...]

    def resolve_executable(self) -> str | None:
        for candidate in self.executables:
            resolved = shutil.which(candidate)
            if resolved:
                return resolved
        return None

    def command(self, task: str) -> list[str]:
        executable = self.resolve_executable()
        if not executable:
            raise FileNotFoundError(f"External child backend '{self.name}' is not installed")
        template = list(self.argv)
        custom = os.getenv(f"KITT_CHILD_BACKEND_{self.name.upper().replace('-', '_')}_COMMAND", "").strip()
        if custom:
            template = shlex.split(custom)
            if not template:
                raise ValueError(f"Empty command override for backend '{self.name}'")
            resolved = shutil.which(template[0])
            if not resolved:
                raise FileNotFoundError(f"Configured executable '{template[0]}' is not available")
            executable = resolved
            template[0] = executable
        elif template:
            template[0] = executable

        used = False
        command: list[str] = []
        for part in template:
            if "{task}" in part:
                command.append(part.replace("{task}", task))
                used = True
            else:
                command.append(part)
        if not used:
            command.append(task)
        return command


class ChildAgentBackendRegistry:
    """Opt-in registry for external child-agent executors.

    External agents never become an implicit fallback. They can only run after
    ``KITT_EXTERNAL_AGENTS_ENABLED`` is enabled and a caller explicitly names a
    backend. KITT's worktree coordinator remains the mutation boundary.
    """

    DEFAULTS = (
        ChildAgentBackend("codex", ("codex",), ("codex", "exec", "--sandbox", "workspace-write", "--skip-git-repo-check", "{task}")),
        ChildAgentBackend("claude", ("claude",), ("claude", "-p", "{task}")),
        ChildAgentBackend("opencode", ("opencode",), ("opencode", "run", "{task}")),
        ChildAgentBackend("aider", ("aider",), ("aider", "--yes-always", "--message", "{task}")),
        ChildAgentBackend("gemini", ("gemini",), ("gemini", "-p", "{task}")),
        ChildAgentBackend("openhands", ("openhands", "openhands-cli"), ("openhands", "--task", "{task}")),
        ChildAgentBackend("prime", ("prime", "prime-agent"), ("prime", "run", "{task}")),
    )

    def __init__(self, backends: Iterable[ChildAgentBackend] | None = None, *, enabled: bool | None = None):
        self._backends = {backend.name: backend for backend in (backends or self.DEFAULTS)}
        self.enabled = (
            str(os.getenv("KITT_EXTERNAL_AGENTS_ENABLED", "")).strip().lower() in _TRUE
            if enabled is None
            else bool(enabled)
        )

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._backends))

    def get(self, name: str) -> ChildAgentBackend:
        key = str(name or "").strip().lower()
        backend = self._backends.get(key)
        if backend is None:
            raise ValueError(f"Unknown external child backend: {name!r}")
        return backend

    def statuses(self) -> list[ChildAgentBackendStatus]:
        result: list[ChildAgentBackendStatus] = []
        for name in self.names():
            backend = self._backends[name]
            executable = backend.resolve_executable()
            result.append(
                ChildAgentBackendStatus(
                    name=name,
                    executable=executable,
                    available=bool(executable),
                    enabled=self.enabled,
                    detail=(
                        "ready for explicit delegation"
                        if executable and self.enabled
                        else "installed but external delegation is disabled"
                        if executable
                        else "executable not found"
                    ),
                )
            )
        return result

    def run(
        self,
        name: str,
        task: str,
        *,
        root_dir: str | Path,
        timeout_seconds: float,
        cancellation: CancellationToken | None = None,
        max_output_bytes: int = 512 * 1024,
    ) -> ExternalBackendResult:
        if not self.enabled:
            raise PermissionError(
                "External retained agents are disabled. Set KITT_EXTERNAL_AGENTS_ENABLED=1 to opt in."
            )
        backend = self.get(name)
        command = backend.command(str(task))
        runner = ProcessRunner(str(Path(root_dir).resolve()), max_output_bytes=max_output_bytes)
        completed = runner.run(
            command,
            timeout_seconds=max(1, min(int(timeout_seconds), 3600)),
            cancellation=cancellation,
        )
        success = completed.returncode == 0 and not completed.timed_out and not completed.cancelled
        output = completed.stdout
        error = None
        if not success:
            if completed.cancelled:
                error = "external child backend cancelled"
            elif completed.timed_out:
                error = "external child backend timed out"
            else:
                error = (completed.stderr or completed.stdout or f"exit code {completed.returncode}")[-4000:]
        return ExternalBackendResult(
            backend=backend.name,
            success=success,
            output=output,
            error=error,
            returncode=completed.returncode,
            duration_ms=completed.duration_ms,
            timed_out=completed.timed_out,
            cancelled=completed.cancelled,
        )
