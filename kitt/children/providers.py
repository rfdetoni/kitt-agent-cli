from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from kitt.core.cancellation import CancellationToken


@dataclass(frozen=True)
class ChildProviderCapabilities:
    continuation: bool = False
    structured_output: bool = False
    model_override: bool = False
    reasoning_override: bool = False
    tool_filter: bool = False
    path_filter: bool = False
    persona: bool = False
    depth_limit: bool = False
    interrupt: bool = True
    image_input: bool = False


@dataclass(frozen=True)
class ChildProviderRequest:
    task: str
    root_dir: str
    timeout_seconds: float
    max_output_bytes: int = 512 * 1024
    model: str | None = None
    reasoning: str | None = None
    persona: str | None = None
    allowed_paths: tuple[str, ...] = ()
    enabled_tools: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChildProviderResult:
    provider: str
    success: bool
    output: str
    error: str | None = None
    returncode: int = 0
    duration_ms: float = 0.0
    timed_out: bool = False
    cancelled: bool = False
    session_id: str | None = None


@runtime_checkable
class ChildProvider(Protocol):
    name: str
    capabilities: ChildProviderCapabilities

    def available(self) -> bool:
        ...

    def run(
        self,
        request: ChildProviderRequest,
        *,
        cancellation: CancellationToken | None = None,
    ) -> ChildProviderResult:
        ...

    def continue_session(
        self,
        session_id: str,
        message: str,
        *,
        cancellation: CancellationToken | None = None,
    ) -> ChildProviderResult:
        ...
