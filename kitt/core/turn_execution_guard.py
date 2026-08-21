from __future__ import annotations

import threading
from collections.abc import MutableSet


class TurnExecutionGuard:
    """Order cancellation against the start of state-changing operations.

    ``begin()`` and ``cancel()`` share one lock. ``cancel()`` returns whether
    at least one operation had already crossed the start barrier. A caller can
    therefore avoid tearing down state that belongs to an operation already in
    flight, while later operations are rejected.
    """

    def __init__(self, cancelled_turns: MutableSet[str] | None = None) -> None:
        self._lock = threading.RLock()
        self._cancelled = cancelled_turns if cancelled_turns is not None else set()
        self._inflight: dict[str, int] = {}

    def cancel(self, turn_id: str) -> bool:
        with self._lock:
            self._cancelled.add(turn_id)
            return self._inflight.get(turn_id, 0) > 0

    def is_cancelled(self, turn_id: str) -> bool:
        with self._lock:
            return turn_id in self._cancelled

    def consume_cancelled(self, turn_id: str) -> bool:
        with self._lock:
            if turn_id not in self._cancelled:
                return False
            self._cancelled.discard(turn_id)
            return True

    def begin(self, turn_id: str) -> bool:
        with self._lock:
            if turn_id in self._cancelled:
                return False
            self._inflight[turn_id] = self._inflight.get(turn_id, 0) + 1
            return True

    def end(self, turn_id: str) -> None:
        with self._lock:
            count = self._inflight.get(turn_id, 0)
            if count <= 1:
                self._inflight.pop(turn_id, None)
            else:
                self._inflight[turn_id] = count - 1

    def has_inflight(self, turn_id: str) -> bool:
        with self._lock:
            return self._inflight.get(turn_id, 0) > 0
