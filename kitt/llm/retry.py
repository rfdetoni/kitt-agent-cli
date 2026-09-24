"""Typed retry policy for transient provider failures."""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass
from typing import Callable, Generator, Tuple, TypeVar

from kitt.llm.domain import (
    ProviderConnectionError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)

T = TypeVar("T")


_TERMINAL_PROVIDER_ERROR_CODES = (
    "agent_contract_invalid",
    "request_id_conflict",
    "conversation_state_conflict",
    "ui_automation_error",
    "content_filter",
    "safety_filter",
    "policy_violation",
)


@dataclass(frozen=True)
class RetryConfig:
    max_retries: int = 10
    base_delay_ms: int = 500
    max_delay_ms: int = 60000
    retryable_status: Tuple[int, ...] = (429, 500, 502, 503, 529)
    retry_timeouts: bool = True
    jitter_ratio: float = 0.25


class RetryPolicy:
    def __init__(self, config: RetryConfig | None = None):
        self.config = config or RetryConfig()

    def is_retryable(self, exc: Exception) -> bool:
        if isinstance(exc, ProviderRateLimitError):
            return True
        if isinstance(exc, ProviderTimeoutError):
            return self.config.retry_timeouts
        if isinstance(exc, ProviderConnectionError):
            msg = str(exc).lower()
            if any(code in msg for code in _TERMINAL_PROVIDER_ERROR_CODES):
                return False
            if "violou o contrato de saída" in msg:
                return False
            status_strings = tuple(str(code) for code in self.config.retryable_status)
            keywords = (
                "rate limit",
                "quota",
                "too many requests",
                "overloaded",
                "temporarily unavailable",
            )
            return any(s in msg for s in status_strings) or any(k in msg for k in keywords)
        return False

    def delay_seconds(self, exc: Exception, attempt: int) -> float:
        if isinstance(exc, ProviderRateLimitError) and exc.retry_after is not None:
            try:
                retry_after = float(exc.retry_after)
            except (TypeError, ValueError):
                retry_after = -1.0
            if math.isfinite(retry_after) and retry_after >= 0:
                return retry_after

        maximum = max(0.0, self.config.max_delay_ms / 1000.0)
        base = min(
            max(0.0, self.config.base_delay_ms / 1000.0) * (2 ** max(0, attempt)),
            maximum,
        )
        ratio = min(1.0, max(0.0, float(self.config.jitter_ratio)))
        low = max(0.0, base * (1.0 - ratio))
        high = min(maximum, base * (1.0 + ratio))
        if high <= low:
            return low
        return random.uniform(low, high)

    def execute_with_retry(
        self,
        fn: Callable[[], Generator[T, None, None]],
    ) -> Generator[T, None, None]:
        for attempt in range(self.config.max_retries + 1):
            emitted = False
            try:
                for item in fn():
                    emitted = True
                    yield item
                return
            except Exception as exc:
                # A request that already exposed output is not replay-safe:
                # repeating it can duplicate visible text or provider side effects.
                if emitted or attempt >= self.config.max_retries or not self.is_retryable(exc):
                    raise
                time.sleep(self.delay_seconds(exc, attempt))
