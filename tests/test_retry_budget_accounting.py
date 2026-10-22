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


def test_shared_cooldown_applies_across_clients_and_honors_cancellation():
    from unittest.mock import patch
    from kitt.llm.retry import ProviderCooldowns
    from kitt.llm.http_security import TransportCancellation, TransportCancelled

    cooldown = ProviderCooldowns()
    clock = [100.0]

    class Cancellation:
        def check(self):
            pass

        def wait(self, seconds):
            clock[0] += seconds

    with patch("kitt.llm.retry.time.monotonic", side_effect=lambda: clock[0]):
        cooldown.record("same-endpoint-and-credential", 80)
        cooldown.wait("same-endpoint-and-credential", Cancellation())
        assert clock[0] == 180.0
        # A distinct provider credential is not held by another key's limit.
        before = clock[0]
        cooldown.wait("different-credential", Cancellation())
        assert clock[0] == before

        cooldown.record("cancelled", 20)
        cancellation = TransportCancellation()
        cancellation.cancel()
        try:
            cooldown.wait("cancelled", cancellation)
        except TransportCancelled:
            pass
        else:
            raise AssertionError("cancelled cooldown must abort")
