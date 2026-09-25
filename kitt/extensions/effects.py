from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any


class EffectScope:
    """Owner-scoped reversible registrations disposed in reverse order."""

    def __init__(self, owner: str):
        self.owner = str(owner)
        self._callbacks: list[Callable[[], Any]] = []
        self._closed = False

    def own(self, cleanup: Callable[[], Any]) -> Callable[[], Any]:
        if self._closed:
            raise RuntimeError(f"EffectScope '{self.owner}' is already disposed")
        if not callable(cleanup):
            raise TypeError("cleanup must be callable")
        self._callbacks.append(cleanup)
        return cleanup

    async def dispose(self) -> list[Exception]:
        if self._closed:
            return []
        self._closed = True
        errors: list[Exception] = []
        while self._callbacks:
            cleanup = self._callbacks.pop()
            try:
                value = cleanup()
                if inspect.isawaitable(value):
                    await value
            except Exception as exc:
                errors.append(exc)
        return errors

    @property
    def closed(self) -> bool:
        return self._closed
