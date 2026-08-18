"""Contract tests for provider runtime adapters, typed errors, and discovery statuses."""
import io
import socket
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from kitt.llm.domain import (
    ProviderAuthError,
    ProviderConnectionError,
    ProviderDiscoveryStatus,
    ProviderModelNotFoundError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from kitt.llm.providers.base import LLMRequest, handle_http_error
from kitt.llm.providers.openai_chat import OpenAIChatAdapter


class TestProviderRuntimeContract(unittest.TestCase):

    def test_handle_http_error_mappings(self):
        # 401 Unauthorized -> ProviderAuthError
        err_401 = urllib.error.HTTPError("http://test", 401, "Unauthorized", {}, io.BytesIO(b'{"error":"invalid_key"}'))
        with self.assertRaises(ProviderAuthError):
            handle_http_error(err_401, "http://test")

        # 404 Not Found -> ProviderModelNotFoundError
        err_404 = urllib.error.HTTPError("http://test", 404, "Not Found", {}, io.BytesIO(b'{"error":"model_not_found"}'))
        with self.assertRaises(ProviderModelNotFoundError):
            handle_http_error(err_404, "http://test")

        # 429 Rate Limit -> ProviderRateLimitError
        err_429 = urllib.error.HTTPError("http://test", 429, "Too Many Requests", {"Retry-After": "10"}, io.BytesIO(b'{"error":"quota_exceeded"}'))
        with self.assertRaises(ProviderRateLimitError) as ctx:
            handle_http_error(err_429, "http://test")
        self.assertEqual(ctx.exception.retry_after, 10.0)

        # 500 Internal Error -> ProviderConnectionError
        err_500 = urllib.error.HTTPError("http://test", 500, "Server Error", {}, io.BytesIO(b'{"error":"internal"}'))
        with self.assertRaises(ProviderConnectionError):
            handle_http_error(err_500, "http://test")

    def test_streaming_and_socket_timeout_handling(self):
        adapter = OpenAIChatAdapter()
        req = LLMRequest(
            model="gpt-4o",
            messages=[{"role": "user", "content": "hello"}],
            base_url="https://api.openai.com",
            timeout_seconds=2,
        )

        with patch("urllib.request.urlopen", side_effect=socket.timeout("Timeout")):
            with self.assertRaises(ProviderTimeoutError):
                list(adapter.stream(req))

    def test_discovery_status_on_auth_failure_vs_success(self):
        adapter = OpenAIChatAdapter()

        # Auth failure (401)
        err_401 = urllib.error.HTTPError("http://test/v1/models", 401, "Unauthorized", {}, io.BytesIO(b'{"error":"invalid"}'))
        with patch("urllib.request.urlopen", side_effect=err_401):
            res = adapter.list_models(base_url="https://api.openai.com", api_key="bad-key")
            self.assertEqual(res.status, ProviderDiscoveryStatus.AUTH_INVALID)
            self.assertEqual(len(res.models), 0)

        # Rate limited (429)
        err_429 = urllib.error.HTTPError("http://test/v1/models", 429, "Rate Limited", {}, io.BytesIO(b'{"error":"rate_limit"}'))
        with patch("urllib.request.urlopen", side_effect=err_429):
            res = adapter.list_models(base_url="https://api.openai.com", api_key="quota-key")
            self.assertEqual(res.status, ProviderDiscoveryStatus.RATE_LIMITED)


if __name__ == "__main__":
    unittest.main()
