from __future__ import annotations

from typing import Any, Iterable


class CompositeEventObserver:
    """Fan out runtime events without letting telemetry affect KITT."""

    def __init__(self, observers: Iterable[Any]):
        self.observers = tuple(observer for observer in observers if observer is not None)

    def observe(self, event: str, payload: Any) -> None:
        for observer in self.observers:
            try:
                observer.observe(event, payload)
            except Exception:
                continue

    def close(self) -> None:
        for observer in self.observers:
            try:
                observer.close()
            except Exception:
                continue
