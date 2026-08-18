"""Thread-safe, priority-ordered HookRegistry with reentrancy protection and cached pipeline chains."""
from __future__ import annotations

import asyncio
import inspect
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar

from kitt.extensions.errors import HookReentrancyError, HookTimeoutError
from kitt.extensions.hooks.models import HookContext, HookRegistration, HookResult

T = TypeVar("T")
MAX_HOOK_DEPTH = 10
DEFAULT_HOOK_TIMEOUT = 5.0

_TLS = threading.local()


def _get_hook_depth() -> Dict[str, int]:
    if not hasattr(_TLS, "depths"):
        _TLS.depths = {}
    return _TLS.depths


class HookRegistry:
    """Central registry for lifecycle hooks, interceptors, and observers."""

    def __init__(self, default_timeout: float = DEFAULT_HOOK_TIMEOUT):
        self.default_timeout = default_timeout
        self._lock = threading.RLock()
        self._registrations: Dict[str, List[HookRegistration]] = {}
        # Cached immutable snapshots: hook_name -> Tuple[HookRegistration, ...]
        self._chain_cache: Dict[str, Tuple[HookRegistration, ...]] = {}

    def register(
        self,
        hook_name: str,
        handler: Callable[..., Any],
        *,
        priority: int = 0,
        plugin_id: str = "",
        fail_closed: bool = False,
        timeout_seconds: Optional[float] = None,
    ) -> HookRegistration:
        """Registers a hook handler. Higher priority runs first."""
        hook = hook_name.strip()
        is_async = inspect.iscoroutinefunction(handler) or inspect.isasyncgenfunction(handler)
        reg = HookRegistration(
            hook_name=hook,
            handler=handler,
            priority=priority,
            plugin_id=plugin_id,
            fail_closed=fail_closed,
            timeout_seconds=timeout_seconds or self.default_timeout,
            is_async=is_async,
        )

        with self._lock:
            if hook not in self._registrations:
                self._registrations[hook] = []
            self._registrations[hook].append(reg)
            self._invalidate_cache(hook)

        return reg

    def unregister(
        self,
        hook_name: Optional[str] = None,
        handler: Optional[Callable[..., Any]] = None,
        plugin_id: Optional[str] = None,
    ) -> int:
        """Unregisters matching hooks by name, handler, or owning plugin_id."""
        removed_count = 0
        with self._lock:
            hooks_to_check = [hook_name] if hook_name else list(self._registrations.keys())
            for h in hooks_to_check:
                if h not in self._registrations:
                    continue
                orig_list = self._registrations[h]
                filtered = []
                for reg in orig_list:
                    match_handler = (handler is None) or (reg.handler == handler)
                    match_plugin = (plugin_id is None) or (reg.plugin_id == plugin_id)
                    if match_handler and match_plugin:
                        removed_count += 1
                    else:
                        filtered.append(reg)
                self._registrations[h] = filtered
                self._invalidate_cache(h)
        return removed_count

    def _invalidate_cache(self, hook_name: str) -> None:
        """Rebuilds the immutable snapshot for the hook sorted by priority descending."""
        if hook_name in self._registrations and self._registrations[hook_name]:
            # Sort stable: -priority preserves FIFO for ties
            sorted_chain = sorted(self._registrations[hook_name], key=lambda r: -r.priority)
            self._chain_cache[hook_name] = tuple(sorted_chain)
        else:
            self._chain_cache[hook_name] = ()

    def get_chain(self, hook_name: str) -> Tuple[HookRegistration, ...]:
        """Returns the cached immutable snapshot of handlers for hook_name."""
        # Fast read outside of lock (dict lookup is atomic in Python)
        chain = self._chain_cache.get(hook_name)
        if chain is not None:
            return chain
        with self._lock:
            self._invalidate_cache(hook_name)
            return self._chain_cache.get(hook_name, ())

    async def run_pipeline(
        self,
        hook_name: str,
        initial_value: T,
        context: Optional[HookContext] = None,
    ) -> HookResult[T]:
        """Executes interceptors sequentially, passing transformed value through the chain."""
        chain = self.get_chain(hook_name)
        if not chain:
            return HookResult(value=initial_value, stop=False)

        depths = _get_hook_depth()
        current_depth = depths.get(hook_name, 0)
        if current_depth >= MAX_HOOK_DEPTH:
            raise HookReentrancyError(
                f"Hook '{hook_name}' exceeded maximum reentrancy depth of {MAX_HOOK_DEPTH}."
            )

        depths[hook_name] = current_depth + 1
        ctx = context or HookContext(hook=hook_name)
        current_val = initial_value

        try:
            for reg in chain:
                try:
                    res = await self._invoke_handler(reg, current_val, ctx)
                    if isinstance(res, HookResult):
                        current_val = res.value
                        if res.stop:
                            return HookResult(value=current_val, stop=True)
                    elif res is not None:
                        current_val = res
                except Exception as exc:
                    if reg.fail_closed:
                        raise exc
                    # Fail-open / log warning and keep current value
                    continue
            return HookResult(value=current_val, stop=False)
        finally:
            depths[hook_name] = current_depth

    async def run_observers(
        self,
        hook_name: str,
        payload: Any,
        context: Optional[HookContext] = None,
    ) -> None:
        """Executes notification hooks without modifying payload."""
        chain = self.get_chain(hook_name)
        if not chain:
            return

        ctx = context or HookContext(hook=hook_name)
        for reg in chain:
            try:
                await self._invoke_handler(reg, payload, ctx)
            except Exception as exc:
                if reg.fail_closed:
                    raise exc
                # Observers fail open by default

    async def _invoke_handler(self, reg: HookRegistration, arg: Any, ctx: HookContext) -> Any:
        """Invokes a sync or async handler respecting timeouts."""
        handler = reg.handler

        # Inspect if handler accepts (arg, ctx) or just (arg)
        try:
            sig = inspect.signature(handler)
            param_count = len(sig.parameters)
        except Exception:
            param_count = 1

        args = (arg, ctx) if param_count >= 2 else (arg,)

        if reg.is_async:
            try:
                return await asyncio.wait_for(handler(*args), timeout=reg.timeout_seconds)
            except asyncio.TimeoutError:
                raise HookTimeoutError(
                    f"Hook handler from '{reg.plugin_id}' for '{reg.hook_name}' timed out after {reg.timeout_seconds}s."
                )
        else:
            # Run sync handler directly or in thread pool if needed
            return handler(*args)
