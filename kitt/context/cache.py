"""L2 LRU cache for CompiledContext keyed by prompt, index and retrieval config."""
from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from kitt.context.compiler import CompiledContext


class ContextCache:
    """Thread-safe LRU cache for compiled context objects.

    ``namespace`` isolates otherwise identical prompt/generation entries when
    the workspace or retrieval configuration changes.
    """

    def __init__(self, maxsize: int = 64):
        self.maxsize = max(1, int(maxsize))
        self._cache: OrderedDict[tuple[str, int, int, str], "CompiledContext"] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def _hash_key(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _key(
        self,
        prompt: str,
        index_generation: int,
        max_tokens: int,
        namespace: str,
    ) -> tuple[str, int, int, str]:
        return (
            self._hash_key(prompt),
            int(index_generation),
            int(max_tokens),
            self._hash_key(namespace) if namespace else "",
        )

    def get(
        self,
        prompt: str,
        index_generation: int,
        max_tokens: int = 0,
        namespace: str = "",
    ) -> Optional["CompiledContext"]:
        key = self._key(prompt, index_generation, max_tokens, namespace)
        with self._lock:
            value = self._cache.get(key)
            if value is not None:
                self._cache.move_to_end(key)
            return value

    def put(
        self,
        prompt: str,
        index_generation: int,
        compiled: "CompiledContext",
        max_tokens: int = 0,
        namespace: str = "",
    ) -> None:
        key = self._key(prompt, index_generation, max_tokens, namespace)
        with self._lock:
            self._cache[key] = compiled
            self._cache.move_to_end(key)
            while len(self._cache) > self.maxsize:
                self._cache.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
