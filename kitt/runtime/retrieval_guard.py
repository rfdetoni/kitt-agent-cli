"""Bounded read/search deduplication and loop suppression."""
from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any


@dataclass
class RetrievalEntry:
    fingerprint: str
    first_tokens: int
    handles: list[str]
    repeats: int
    last_seen: float
    generation: int


class RetrievalGuard:
    """Collapse unchanged repeated retrievals without serving stale cached content.

    The underlying operation still executes, so current content/index state is
    observed before deduplication. This deliberately trades a small amount of I/O
    for correctness: external edits are never hidden by a stale cache.
    """

    def __init__(self, max_entries: int = 256, ttl_seconds: float = 900.0):
        self.max_entries = max(32, int(max_entries))
        self.ttl_seconds = max(30.0, float(ttl_seconds))
        self._entries: OrderedDict[str, RetrievalEntry] = OrderedDict()
        self._generation = 0

    @staticmethod
    def _tokens(value: Any) -> int:
        payload = json.dumps(
            value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")
        ).encode("utf-8")
        return (len(payload) + 3) // 4

    @staticmethod
    def _key(operation: str, arguments: dict[str, Any]) -> str:
        normalized = {
            key: value
            for key, value in arguments.items()
            if key not in {"force_refresh", "max_tokens", "token_budget"}
        }
        raw = json.dumps(
            [operation, normalized],
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _fingerprint(data: Any, metadata: dict[str, Any]) -> str:
        strong = (
            metadata.get("full_file_hash")
            or metadata.get("content_hash")
            or metadata.get("index_generation")
        )
        if strong:
            return str(strong)
        raw = json.dumps(
            data, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def invalidate(self) -> None:
        self._generation += 1
        self._entries.clear()

    def _prune(self, now: float) -> None:
        expired = [
            key
            for key, entry in self._entries.items()
            if now - entry.last_seen > self.ttl_seconds
        ]
        for key in expired:
            self._entries.pop(key, None)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def observe(self, operation: str, arguments: dict[str, Any], result):
        if not getattr(result, "success", False):
            return result
        if arguments.get("force_refresh"):
            return result

        now = time.monotonic()
        self._prune(now)
        key = self._key(operation, arguments)
        metadata = dict(getattr(result, "metadata", {}) or {})
        fingerprint = self._fingerprint(getattr(result, "data", None), metadata)
        current_tokens = self._tokens(getattr(result, "data", None))
        entry = self._entries.get(key)

        if (
            entry is None
            or entry.generation != self._generation
            or entry.fingerprint != fingerprint
        ):
            self._entries[key] = RetrievalEntry(
                fingerprint=fingerprint,
                first_tokens=current_tokens,
                handles=list(getattr(result, "context_handles", []) or []),
                repeats=1,
                last_seen=now,
                generation=self._generation,
            )
            self._entries.move_to_end(key)
            self._prune(now)
            return result

        entry.repeats += 1
        entry.last_seen = now
        self._entries.move_to_end(key)
        stub = {
            "deduplicated": True,
            "unchanged": True,
            "repeat": entry.repeats,
            "operation": operation,
            "context_handles": entry.handles,
            "hint": (
                "Use the previous result/handle. Pass force_refresh=true only "
                "when an external change is expected."
            ),
        }
        stub_tokens = self._tokens(stub)
        saved = max(0, current_tokens - stub_tokens)
        result.data = stub
        result.tokens_saved = int(getattr(result, "tokens_saved", 0) or 0) + saved
        metadata.update(
            {
                "deduplicated": True,
                "repeat_count": entry.repeats,
                "raw_estimated_tokens": max(
                    int(metadata.get("raw_estimated_tokens", 0) or 0),
                    current_tokens,
                ),
                "output_estimated_tokens": stub_tokens,
                "tokens_saved": int(metadata.get("tokens_saved", 0) or 0) + saved,
                "context_avoidance_tokens": int(
                    metadata.get("context_avoidance_tokens", 0) or 0
                ) + saved,
            }
        )
        result.metadata = metadata

        if entry.repeats >= 4:
            result.success = False
            result.error = (
                f"Repeated unchanged {operation} loop detected "
                f"({entry.repeats} identical retrievals). Reuse the prior "
                "context handle or change the query/range."
            )
        return result
