"""L2 LRU cache for CompiledContext based on prompt hash and index generation."""
from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from kitt.context.compiler import CompiledContext


class ContextCache:
    """LRU Cache for compiled context objects."""

    def __init__(self, maxsize: int = 64):
        self.maxsize = maxsize
        self._cache: OrderedDict[tuple[str, int, int], "CompiledContext"] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def _hash_key(prompt: str) -> str:
        return hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    def get(self, prompt: str, index_generation: int, max_tokens: int = 0) -> Optional["CompiledContext"]:
        key = (self._hash_key(prompt), index_generation, max_tokens)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        return None

    def put(self, prompt: str, index_generation: int, compiled: "CompiledContext", max_tokens: int = 0) -> None:
        key = (self._hash_key(prompt), index_generation, max_tokens)
        with self._lock:
            self._cache[key] = compiled
            self._cache.move_to_end(key)
            if len(self._cache) > self.maxsize:
                self._cache.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
