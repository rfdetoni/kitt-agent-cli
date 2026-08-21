from __future__ import annotations

import threading
from collections.abc import MutableSet


class TurnExecutionGuard:
    """Establish ordering between cancellation and tool/mutation start.

    ``begin()`` and ``cancel()`` share one lock. Therefore, once ``cancel()``
    returns, a later tool cannot transition into the started state. Operations
    that called ``begin()`` first are explicitly considered already in-flight
    and may complete cooperatively.
    """

    def __init__(self, cancelled_turns: MutableSet[str] | None = None) -> None:
        self._lock = threading.RLock()
        self._cancelled = cancelled_turns if cancelled_turns is not None else set()
        self._inflight: dict[str, int] = {}

    def cancel(self, turn_id: str) -> None:
        with self._lock:
            self._cancelled.add(turn_id)

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
