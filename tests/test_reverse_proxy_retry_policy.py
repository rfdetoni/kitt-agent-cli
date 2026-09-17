import unittest

from kitt.domain.entities import ModelProfile
from kitt.llm.client import LLMClient, LLMConnectionError, LLMTimeoutError
from kitt.llm.retry import RetryConfig, RetryPolicy


class ReverseProxyRetryPolicyTests(unittest.TestCase):
    def test_timeout_retry_can_be_disabled_for_side_effectful_chat_requests(self):
        attempts = 0

        def stream():
            nonlocal attempts
            attempts += 1
            raise LLMTimeoutError("browser response timed out")
            yield  # pragma: no cover

        policy = RetryPolicy(
            RetryConfig(
                max_retries=2,
                base_delay_ms=0,
                max_delay_ms=0,
                retry_timeouts=False,
            )
        )

        with self.assertRaises(LLMTimeoutError):
            list(policy.execute_with_retry(stream))

        self.assertEqual(attempts, 1)

    def test_reverse_proxy_client_disables_timeout_retries_by_default(self):
        profile = ModelProfile(
            backend="kitt-reverse-proxy",
            model="chatgpt-web",
            base_url="http://127.0.0.1:3000",
            protocol="kitt-reverse-proxy",
        )
        client = LLMClient(profile)
        try:
            self.assertEqual(client.retry_policy.config.max_retries, 2)
            self.assertFalse(client.retry_policy.config.retry_timeouts)
        finally:
            client.close()

    def test_default_policy_keeps_timeout_retry_behavior_for_other_providers(self):
        self.assertTrue(RetryPolicy().config.retry_timeouts)

    def test_terminal_reverse_proxy_errors_are_not_retryable(self):
        policy = RetryPolicy()
        for code in (
            "agent_contract_invalid",
            "request_id_conflict",
            "conversation_state_conflict",
            "ui_automation_error",
        ):
            with self.subTest(code=code):
                error = LLMConnectionError(f'HTTP 502: {{"error":{{"code":"{code}"}}}}')
                self.assertFalse(policy.is_retryable(error))

    def test_generic_502_remains_retryable(self):
        self.assertTrue(RetryPolicy().is_retryable(LLMConnectionError("HTTP 502: Bad Gateway")))


if __name__ == "__main__":
    unittest.main()
