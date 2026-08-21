from __future__ import annotations

import threading
from dataclasses import dataclass


class CancelledError(RuntimeError):
    pass


class CancellationToken:
    """Thread-safe cooperative cancellation token."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise CancelledError("Operation cancelled")


@dataclass(frozen=True)
class CancellationSnapshot:
    turn_id: str
    cancelled: bool


class CancellationRegistry:
    """Own cancellation tokens per turn and make cross-thread checks explicit."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tokens: dict[str, CancellationToken] = {}

    def token(self, turn_id: str) -> CancellationToken:
        if not turn_id:
            raise ValueError("turn_id is required")
        with self._lock:
            return self._tokens.setdefault(turn_id, CancellationToken())

    def cancel(self, turn_id: str) -> None:
        self.token(turn_id).cancel()

    def is_cancelled(self, turn_id: str) -> bool:
        with self._lock:
            token = self._tokens.get(turn_id)
            return bool(token and token.cancelled)

    def discard(self, turn_id: str) -> None:
        with self._lock:
            self._tokens.pop(turn_id, None)

    def snapshot(self, turn_id: str) -> CancellationSnapshot:
        return CancellationSnapshot(turn_id=turn_id, cancelled=self.is_cancelled(turn_id))
