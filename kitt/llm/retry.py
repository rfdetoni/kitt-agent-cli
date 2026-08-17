"""Retry policy and configuration with exponential backoff and jitter."""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable, Generator, TypeVar, Tuple

T = TypeVar("T")


@dataclass(frozen=True)
class RetryConfig:
    max_retries: int = 10
    base_delay_ms: int = 500
    max_delay_ms: int = 60000
    retryable_status: Tuple[int, ...] = (429, 500, 502, 503, 529)


class RetryPolicy:
    def __init__(self, config: RetryConfig | None = None):
        self.config = config or RetryConfig()

    def is_retryable(self, exc: Exception) -> bool:
        from kitt.llm.client import LLMTimeoutError, LLMConnectionError
        if isinstance(exc, LLMTimeoutError):
            return True
        if isinstance(exc, LLMConnectionError):
            msg = str(exc).lower()
            status_strings = tuple(str(code) for code in self.config.retryable_status)
            keywords = ("rate limit", "quota", "too many requests", "overloaded", "temporarily unavailable")
            return any(s in msg for s in status_strings) or any(k in msg for k in keywords)
        return False

    def execute_with_retry(self, fn: Callable[[], Generator[T, None, None]]) -> Generator[T, None, None]:
        for attempt in range(self.config.max_retries + 1):
            try:
                yield from fn()
                return
            except Exception as exc:
                if attempt >= self.config.max_retries or not self.is_retryable(exc):
                    raise
                delay_sec = min(
                    (self.config.base_delay_ms / 1000.0) * (2 ** attempt) + random.uniform(0.0, 0.3),
                    self.config.max_delay_ms / 1000.0,
                )
                time.sleep(delay_sec)
