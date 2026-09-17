"""UI policy for reasoning controls owned outside the KITT agent.

When execution uses kitt-reverse-proxy, the authenticated WebChat is the sole
source of truth for reasoning/model thinking level. KITT must not expose a
control that suggests it can change that WebChat setting.
"""
from __future__ import annotations

from typing import Any, Callable


_REVERSE_PROXY_IDS = {"kitt-reverse-proxy", "kitt-proxy"}
_REASONING_ALIASES = {"/reasoning", "/think", "/effort"}


def reverse_proxy_execution_active(app: Any) -> bool:
    """Return True when the currently resolved execution profile is the proxy."""
    try:
        processor = getattr(getattr(app, "runtime", None), "processor", None)
        router = getattr(processor, "router", None)
        if router is None:
            return False
        _, profile = router.resolve_profile_for_task("code-generation")
        backend = str(getattr(profile, "backend", "") or "").strip().lower()
        protocol = str(getattr(profile, "protocol", "") or "").strip().lower()
        return backend in _REVERSE_PROXY_IDS or protocol == "kitt-reverse-proxy"
    except Exception:
        return False


def _strip_reasoning_display(value: Any) -> Any:
    """Remove reasoning-specific UI fragments while preserving the surface type."""
    if isinstance(value, str):
        lines = value.splitlines(keepends=True)
        return "".join(
            line for line in lines
            if "reasoning:" not in line.casefold() and "🧠" not in line
        )
    if isinstance(value, list):
        filtered = []
        for item in value:
            text = item[1] if isinstance(item, tuple) and len(item) > 1 else item
            rendered = str(text)
            if "reasoning:" in rendered.casefold() or "🧠" in rendered:
                continue
            filtered.append(item)
        return filtered
    return value


class ReverseProxyAwareCommandRegistry:
    """Filter reasoning commands only while reverse-proxy execution is active."""

    def __init__(self, delegate: Any, hidden: Callable[[], bool]):
        self._delegate = delegate
        self._hidden = hidden

    @property
    def commands(self):
        commands = self._delegate.commands
        if not self._hidden():
            return commands
        return {key: value for key, value in commands.items() if key != "reasoning"}

    def register(self, spec):
        return self._delegate.register(spec)

    def search(self, query: str):
        matches = self._delegate.search(query)
        if not self._hidden():
            return matches
        return [item for item in matches if item.id != "reasoning"]

    def find(self, name: str):
        found = self._delegate.find(name)
        if self._hidden() and found is not None and found.id == "reasoning":
            return None
        return found

    def resolve(self, raw: str):
        found, arg = self._delegate.resolve(raw)
        if self._hidden() and found is not None and found.id == "reasoning":
            return None, ""
        return found, arg


def install_reverse_proxy_reasoning_policy(app_cls: type) -> None:
    """Install an idempotent provider-aware UI policy on KittUIApp."""
    if getattr(app_cls, "_reverse_proxy_reasoning_policy_installed", False):
        return

    original_init = app_cls.__init__
    original_execute_command = app_cls._execute_command
    original_set_reasoning = app_cls._set_reasoning_effort
    original_header_text = app_cls._header_text
    original_sidebar_text = app_cls._sidebar_text

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.commands = ReverseProxyAwareCommandRegistry(
            self.commands,
            lambda: reverse_proxy_execution_active(self),
        )

    async def patched_execute_command(self, raw: str) -> bool:
        first = str(raw or "").strip().split(maxsplit=1)[0].casefold() if str(raw or "").strip() else ""
        if reverse_proxy_execution_active(self) and first in _REASONING_ALIASES:
            # Consume legacy commands silently. They must never become WebChat
            # prompts and must never suggest that KITT controls WebChat reasoning.
            return True
        return await original_execute_command(self, raw)

    async def patched_set_reasoning(self, value: int, *, notify: bool = True) -> None:
        if reverse_proxy_execution_active(self):
            return
        await original_set_reasoning(self, value, notify=notify)

    def patched_header_text(self):
        value = original_header_text(self)
        return _strip_reasoning_display(value) if reverse_proxy_execution_active(self) else value

    def patched_sidebar_text(self):
        value = original_sidebar_text(self)
        return _strip_reasoning_display(value) if reverse_proxy_execution_active(self) else value

    app_cls.__init__ = patched_init
    app_cls._execute_command = patched_execute_command
    app_cls._set_reasoning_effort = patched_set_reasoning
    app_cls._header_text = patched_header_text
    app_cls._sidebar_text = patched_sidebar_text
    app_cls._reverse_proxy_reasoning_policy_installed = True


__all__ = [
    "ReverseProxyAwareCommandRegistry",
    "install_reverse_proxy_reasoning_policy",
    "reverse_proxy_execution_active",
]
