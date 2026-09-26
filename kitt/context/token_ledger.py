"""Incremental prompt/message token accounting.

The estimator itself is deliberately lightweight. The ledger prevents unchanged
messages from being re-estimated on every tool-loop rebudget pass while keeping
correctness when message dictionaries are mutated in place.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from kitt.context.token_estimator import CalibratedTokenEstimator


@dataclass(frozen=True)
class _MessageEntry:
    role: str
    content: str
    tokens: int


class TokenLedger:
    """Cache token estimates for one evolving execution transcript."""

    def __init__(self, estimator: CalibratedTokenEstimator | None = None):
        self.estimator = estimator or CalibratedTokenEstimator()
        self._system_text = ""
        self._system_tokens = 0
        self._messages: dict[int, _MessageEntry] = {}
        self.reused_entries = 0
        self.recomputed_entries = 0

    def reset(self) -> None:
        self._system_text = ""
        self._system_tokens = 0
        self._messages.clear()
        self.reused_entries = 0
        self.recomputed_entries = 0

    def count_text(self, text: str) -> int:
        return self.estimator.count_text(text or "").count

    def system_tokens(self, system_prompt: str) -> int:
        text = system_prompt or ""
        if text != self._system_text:
            self._system_text = text
            self._system_tokens = self.count_text(text)
        return self._system_tokens

    def message_tokens(self, messages: Sequence[dict[str, Any]]) -> int:
        total = 0
        next_entries: dict[int, _MessageEntry] = {}
        for message in messages:
            role = str(message.get("role", ""))
            content = str(message.get("content", "") or "")
            key = id(message)
            cached = self._messages.get(key)
            if cached and cached.role == role and cached.content == content:
                entry = cached
                self.reused_entries += 1
            else:
                entry = _MessageEntry(
                    role=role,
                    content=content,
                    tokens=self.count_text(content) + 4,
                )
                self.recomputed_entries += 1
            next_entries[key] = entry
            total += entry.tokens
        self._messages = next_entries
        return total

    def total_input_tokens(
        self,
        system_prompt: str,
        messages: Sequence[dict[str, Any]],
    ) -> int:
        return self.system_tokens(system_prompt) + self.message_tokens(messages)

    def snapshot(self) -> dict[str, int]:
        return {
            "system_tokens": self._system_tokens,
            "message_entries": len(self._messages),
            "reused_entries": self.reused_entries,
            "recomputed_entries": self.recomputed_entries,
        }
