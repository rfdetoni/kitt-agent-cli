from __future__ import annotations

import os
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from kitt.children.providers import (
    ChildProvider,
    ChildProviderCapabilities,
    ChildProviderRequest,
    ChildProviderResult,
)
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
    """One explicitly selected external coding-agent CLI."""

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
        custom = os.getenv(
            f"KITT_CHILD_BACKEND_{self.name.upper().replace('-', '_')}_COMMAND",
            "",
        ).strip()
        if custom:
            template = shlex.split(custom)
            if not template:
                raise ValueError(f"Empty command override for backend '{self.name}'")
            resolved = shutil.which(template[0])
            if not resolved:
                raise FileNotFoundError(
                    f"Configured executable '{template[0]}' is not available"
                )
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


class ExternalCliChildProvider:
    """Provider adapter around an argv-only external agent backend."""

    capabilities = ChildProviderCapabilities(
        continuation=False,
        structured_output=False,
        model_override=False,
        reasoning_override=False,
        tool_filter=False,
        path_filter=False,
        persona=False,
        depth_limit=False,
        interrupt=True,
        image_input=False,
    )

    def __init__(self, backend: ChildAgentBackend):
        self.backend = backend
        self.name = backend.name

    def available(self) -> bool:
        return self.backend.resolve_executable() is not None

    def run(
        self,
        request: ChildProviderRequest,
        *,
        cancellation: CancellationToken | None = None,
    ) -> ChildProviderResult:
        command = self.backend.command(request.task)
        runner = ProcessRunner(
            str(Path(request.root_dir).resolve()),
            max_output_bytes=request.max_output_bytes,
        )
        completed = runner.run(
            command,
            timeout_seconds=max(1, min(int(request.timeout_seconds), 3600)),
            cancellation=cancellation,
        )
        success = (
            completed.returncode == 0
            and not completed.timed_out
            and not completed.cancelled
        )
        error = None
        if not success:
            if completed.cancelled:
                error = "external child provider cancelled"
            elif completed.timed_out:
                error = "external child provider timed out"
            else:
                error = (
                    completed.stderr
                    or completed.stdout
                    or f"exit code {completed.returncode}"
                )[-4000:]
        return ChildProviderResult(
            provider=self.name,
            success=success,
            output=completed.stdout,
            error=error,
            returncode=completed.returncode,
            duration_ms=completed.duration_ms,
            timed_out=completed.timed_out,
            cancelled=completed.cancelled,
        )

    def continue_session(
        self,
        session_id: str,
        message: str,
        *,
        cancellation: CancellationToken | None = None,
    ) -> ChildProviderResult:
        return ChildProviderResult(
            provider=self.name,
            success=False,
            output="",
            error=(
                f"provider '{self.name}' does not support retained continuation; "
                "start a new delegated task instead"
            ),
            session_id=session_id,
        )


class ChildAgentBackendRegistry:
    """Capability-oriented child-provider registry with legacy CLI compatibility."""

    DEFAULTS = (
        ChildAgentBackend(
            "codex",
            ("codex",),
            ("codex", "exec", "--sandbox", "workspace-write", "--skip-git-repo-check", "{task}"),
        ),
        ChildAgentBackend("claude", ("claude",), ("claude", "-p", "{task}")),
        ChildAgentBackend("opencode", ("opencode",), ("opencode", "run", "{task}")),
        ChildAgentBackend("aider", ("aider",), ("aider", "--yes-always", "--message", "{task}")),
        ChildAgentBackend("gemini", ("gemini",), ("gemini", "-p", "{task}")),
        ChildAgentBackend("openhands", ("openhands", "openhands-cli"), ("openhands", "--task", "{task}")),
        ChildAgentBackend("prime", ("prime", "prime-agent"), ("prime", "run", "{task}")),
    )

    def __init__(
        self,
        backends: Iterable[ChildAgentBackend] | None = None,
        *,
        providers: Iterable[ChildProvider] | None = None,
        enabled: bool | None = None,
    ):
        configured = tuple(backends or self.DEFAULTS)
        self._backends = {backend.name: backend for backend in configured}
        self._providers: dict[str, ChildProvider] = {
            backend.name: ExternalCliChildProvider(backend)
            for backend in configured
        }
        for provider in providers or ():
            if not provider.name:
                raise ValueError("child provider name is required")
            self._providers[provider.name] = provider
        self.enabled = (
            str(os.getenv("KITT_EXTERNAL_AGENTS_ENABLED", "")).strip().lower() in _TRUE
            if enabled is None
            else bool(enabled)
        )

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))

    def get(self, name: str) -> ChildAgentBackend:
        key = str(name or "").strip().lower()
        backend = self._backends.get(key)
        if backend is None:
            raise ValueError(f"External child provider '{name}' is not argv-backed")
        return backend

    def provider(self, name: str) -> ChildProvider:
        key = str(name or "").strip().lower()
        provider = self._providers.get(key)
        if provider is None:
            raise ValueError(f"Unknown external child provider: {name!r}")
        return provider

    def capabilities(self, name: str) -> ChildProviderCapabilities:
        return self.provider(name).capabilities

    def statuses(self) -> list[ChildAgentBackendStatus]:
        result: list[ChildAgentBackendStatus] = []
        for name in self.names():
            provider = self._providers[name]
            backend = self._backends.get(name)
            executable = backend.resolve_executable() if backend is not None else None
            available = bool(provider.available())
            result.append(
                ChildAgentBackendStatus(
                    name=name,
                    executable=executable,
                    available=available,
                    enabled=self.enabled,
                    detail=(
                        "ready for explicit delegation"
                        if available and self.enabled
                        else "installed but external delegation is disabled"
                        if available
                        else "provider unavailable"
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
                "External retained agents are disabled. "
                "Set KITT_EXTERNAL_AGENTS_ENABLED=1 to opt in."
            )
        result = self.provider(name).run(
            ChildProviderRequest(
                task=str(task),
                root_dir=str(Path(root_dir).resolve()),
                timeout_seconds=timeout_seconds,
                max_output_bytes=max_output_bytes,
            ),
            cancellation=cancellation,
        )
        return ExternalBackendResult(
            backend=result.provider,
            success=result.success,
            output=result.output,
            error=result.error,
            returncode=result.returncode,
            duration_ms=result.duration_ms,
            timed_out=result.timed_out,
            cancelled=result.cancelled,
        )

    def continue_session(
        self,
        name: str,
        session_id: str,
        message: str,
        *,
        cancellation: CancellationToken | None = None,
    ) -> ChildProviderResult:
        if not self.enabled:
            raise PermissionError(
                "External retained agents are disabled. "
                "Set KITT_EXTERNAL_AGENTS_ENABLED=1 to opt in."
            )
        return self.provider(name).continue_session(
            session_id,
            message,
            cancellation=cancellation,
        )
