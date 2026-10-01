from __future__ import annotations

from kitt.llm.domain import ProviderConnectionError
from kitt.llm.retry import RetryConfig, RetryPolicy


def test_retry_policy_reports_every_real_provider_attempt():
    policy = RetryPolicy(
        RetryConfig(
            max_retries=2,
            base_delay_ms=0,
            max_delay_ms=0,
            jitter_ratio=0.0,
        )
    )
    attempts = []
    invocations = {"count": 0}

    def stream():
        invocations["count"] += 1
        if invocations["count"] == 1:
            raise ProviderConnectionError("503 temporarily unavailable")
        yield "ok"

    result = "".join(
        policy.execute_with_retry(stream, on_attempt=attempts.append)
    )

    assert result == "ok"
    assert invocations["count"] == 2
    assert attempts == [0, 1]
