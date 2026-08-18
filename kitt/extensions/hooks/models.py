"""Data models for hooks, interceptors, and pipeline results."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Generic, Mapping, Optional, TypeVar

T = TypeVar("T")


@dataclass
class HookContext:
    """Context passed to hook handlers containing execution metadata."""
    hook: str
    plugin_id: Optional[str] = None
    session_id: Optional[str] = None
    turn_id: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class HookResult(Generic[T]):
    """Result of an interceptor pipeline containing the final transformed value."""
    value: T
    stop: bool = False
    error: Optional[Exception] = None


@dataclass(frozen=True)
class HookRegistration:
    hook_name: str
    handler: Callable[..., Any]
    priority: int = 0
    plugin_id: str = ""
    fail_closed: bool = False
    timeout_seconds: float = 5.0
    is_async: bool = False
