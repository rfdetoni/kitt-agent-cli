"""Contract tests for provider runtime adapters, typed errors, and discovery statuses."""
import io
import json
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
    ProviderProtocolError,
)
from kitt.llm.http_security import secure_urlopen
from kitt.llm.providers.base import LLMRequest, handle_http_error
from kitt.llm.providers.openai_chat import OpenAIChatAdapter


class TestProviderRuntimeContract(unittest.TestCase):

    def test_freellmapi_uses_bounded_gateway_retries(self):
        from kitt.domain.entities import ModelProfile
        from kitt.llm.client import LLMClient
        with LLMClient(ModelProfile(backend="freellmapi", model="auto")) as client:
            self.assertEqual(client.retry_policy.config.max_retries, 1)
            self.assertTrue(client.retry_policy.config.only_rate_limits)
            self.assertFalse(client.retry_policy.config.retry_timeouts)

    def test_freellmapi_waits_for_retry_after_once_and_succeeds(self):
        from kitt.domain.entities import ModelProfile
        from kitt.llm.client import LLMClient
        registry = MagicMock()
        adapter = registry.get_adapter_for_protocol.return_value

        def upstream(_request):
            if adapter.stream.call_count == 1:
                raise ProviderRateLimitError("Gateway cooling down", retry_after=2)
            return iter(["ok"])

        adapter.stream.side_effect = upstream
        profile = ModelProfile(backend="freellmapi", model="auto", protocol="openai-compatible")
        with patch("kitt.llm.client.resolve_endpoint_credential", return_value="fixture-key"), patch(
            "kitt.llm.client._FREE_LLM_API_COOLDOWNS"
        ) as cooldown, patch(
            "kitt.llm.http_security.TransportCancellation.wait"
        ) as wait:
            with LLMClient(profile, registry=registry) as client:
                self.assertEqual(
                    list(client.chat_stream([{"role": "user", "content": "Run"}])),
                    ["ok"],
                )
        self.assertEqual(adapter.stream.call_count, 2)
        cooldown.record.assert_called_once()
        self.assertEqual(wait.call_args.args[0], 2.0)

    def test_freellmapi_long_quota_429_returns_without_waiting(self):
        from kitt.domain.entities import ModelProfile
        from kitt.llm.client import LLMClient
        registry = MagicMock()
        adapter = registry.get_adapter_for_protocol.return_value
        adapter.stream.side_effect = ProviderRateLimitError(
            "Daily quota exhausted", retry_after=3600
        )
        profile = ModelProfile(backend="freellmapi", model="auto", protocol="openai-compatible")
        with patch("kitt.llm.client.resolve_endpoint_credential", return_value=None), patch(
            "kitt.llm.client._FREE_LLM_API_COOLDOWNS"
        ), patch("kitt.llm.http_security.TransportCancellation.wait") as wait:
            with LLMClient(profile, registry=registry) as client:
                with self.assertRaises(ProviderRateLimitError):
                    list(client.chat_stream([{"role": "user", "content": "Run"}]))
        self.assertEqual(adapter.stream.call_count, 1)
        wait.assert_not_called()

    def test_freellmapi_never_retries_mixed_gateway_502(self):
        from kitt.domain.entities import ModelProfile
        from kitt.llm.client import LLMClient
        registry = MagicMock()
        adapter = registry.get_adapter_for_protocol.return_value
        adapter.stream.side_effect = ProviderConnectionError(
            "HTTP 502: upstream_failed (rate_limited and provider_bad_request)"
        )
        profile = ModelProfile(backend="freellmapi", model="auto", protocol="openai-compatible")
        with patch("kitt.llm.client.resolve_endpoint_credential", return_value=None), patch(
            "kitt.llm.client._FREE_LLM_API_COOLDOWNS"
        ), patch("kitt.llm.http_security.TransportCancellation.wait") as wait:
            with LLMClient(profile, registry=registry) as client:
                with self.assertRaises(ProviderConnectionError):
                    list(client.chat_stream([{"role": "user", "content": "Run"}]))
        self.assertEqual(adapter.stream.call_count, 1)
        wait.assert_not_called()

    def test_freellmapi_parses_absolute_retry_timestamp(self):
        import time
        retry_at = int((time.time() + 80) * 1000)
        body = json.dumps({"error": {"code": "rate_limit_exceeded", "retryAtMs": retry_at}})
        error = urllib.error.HTTPError(
            "http://localhost:3001/v1/chat/completions", 429,
            "Too Many Requests", {}, io.BytesIO(body.encode())
        )
        with self.assertRaises(ProviderRateLimitError) as caught:
            handle_http_error(error, error.url)
        self.assertGreater(caught.exception.retry_after, 78)
        self.assertLessEqual(caught.exception.retry_after, 81)

    def test_freellmapi_parses_routing_exhaustion_eta(self):
        body = json.dumps({"error": {
            "code": "routing_exhausted",
            "message": "All models exhausted. Soonest reset ~69s."
        }})
        error = urllib.error.HTTPError(
            "http://localhost:3001/v1/chat/completions", 429,
            "Too Many Requests", {}, io.BytesIO(body.encode())
        )
        with self.assertRaises(ProviderRateLimitError) as caught:
            handle_http_error(error, error.url)
        self.assertEqual(caught.exception.retry_after, 69)

    def test_native_tool_stream_and_host_result_round_trip(self):
        sent = []
        events = [
            {"choices": [{"delta": {"tool_calls": [{"index": 3, "id": "call_1", "function": {"name": "read_file", "arguments": '{"pa'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 3, "function": {"arguments": 'th":"README.md"}'}}]}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}},
        ]
        raw = b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events) + b"data: [DONE]\n\n"
        def respond(request, **kwargs):
            sent.append(json.loads(request.data))
            return io.BytesIO(raw)
        usage = []
        request = LLMRequest(
            model="free-model", messages=[{"role": "user", "content": "Read README.md"}],
            tool_definitions=[{"name": "read_file", "args": {"path": "string"}}],
            usage_callback=usage.append,
        )
        with patch("kitt.llm.providers.openai_chat.secure_urlopen", side_effect=respond):
            bridge = "".join(OpenAIChatAdapter().stream(request))
            request.messages.extend([
                {"role": "assistant", "content": bridge},
                {"role": "user", "content": "Result from the host: README contents"},
            ])
            list(OpenAIChatAdapter().stream(request))
        self.assertIn('"name": "read_file"', bridge)
        self.assertEqual(sent[0]["tools"][0]["function"]["name"], "read_file")
        self.assertFalse(sent[0]["parallel_tool_calls"])
        self.assertEqual(sent[1]["messages"][-2]["tool_calls"][0]["id"], "call_1")
        self.assertEqual(sent[1]["messages"][-1]["tool_call_id"], "call_1")
        self.assertEqual(sent[1]["messages"][-1]["role"], "tool")
        self.assertEqual(usage[0]["total_tokens"], 30)

    def test_stream_errors_are_not_silently_discarded(self):
        raw = b'data: {"error":{"message":"No usable free model test-gateway-secret"}}\n\ndata: [DONE]\n\n'
        with patch("kitt.llm.providers.openai_chat.secure_urlopen", return_value=io.BytesIO(raw)):
            with self.assertRaisesRegex(ProviderProtocolError, "No usable free model") as caught:
                list(OpenAIChatAdapter().stream(LLMRequest(model="auto", messages=[], api_key="test-gateway-secret")))
            self.assertNotIn("test-gateway-secret", str(caught.exception))

    def test_native_call_outside_allowlist_is_rejected_before_emission(self):
        for function in [
            {"name": "run_command", "arguments": '{"argv":["id"]}'},
            {"name": "read_file", "arguments": '[]'},
            {"name": "read_file", "arguments": '{"path":'},
        ]:
            with self.subTest(function=function):
                event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": function}]}, "finish_reason": "tool_calls"}]}
                raw = b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"
                request = LLMRequest(model="auto", messages=[], tool_definitions=[{"name": "read_file", "args": {"path": "string"}}])
                with patch("kitt.llm.providers.openai_chat.secure_urlopen", return_value=io.BytesIO(raw)):
                    with self.assertRaises(ProviderProtocolError):
                        next(OpenAIChatAdapter().stream(request))

    def test_parallel_native_calls_are_rejected_without_emitting_an_action(self):
        from kitt.llm.domain import ProviderRecoverableError
        parts = [{"index": index, "id": "call_" + str(index), "function": {"name": "read_file", "arguments": '{"path":"README.md"}'}} for index in (0, 1)]
        raw = b"data: " + json.dumps({"choices": [{"delta": {"tool_calls": parts}}]}).encode() + b"\n\ndata: [DONE]\n\n"
        request = LLMRequest(model="auto", messages=[], tool_definitions=[{"name": "read_file", "args": {"path": "string"}}])
        with patch("kitt.llm.providers.openai_chat.secure_urlopen", return_value=io.BytesIO(raw)):
            with self.assertRaises(ProviderRecoverableError) as caught:
                next(OpenAIChatAdapter().stream(request))
        self.assertEqual(caught.exception.code, "native_tool_call_invalid")

    def test_client_only_requests_json_mode_when_profile_supports_it(self):
        from kitt.domain.entities import ModelProfile
        from kitt.llm.client import LLMClient

        for supports_json in (False, True):
            with self.subTest(supports_json=supports_json):
                registry = MagicMock()
                adapter = registry.get_adapter_for_protocol.return_value
                adapter.stream.return_value = iter(["{}"])
                profile = ModelProfile(
                    backend="freellmapi", model="auto", protocol="openai-compatible",
                    supports_json=supports_json,
                )
                with patch("kitt.llm.client.resolve_endpoint_credential", return_value=None):
                    with LLMClient(profile, registry=registry) as client:
                        self.assertEqual("".join(client.chat_stream(
                            [{"role": "user", "content": "Return JSON"}],
                            response_format="json",
                        )), "{}")
                request = adapter.stream.call_args.args[0]
                self.assertEqual(request.response_format, "json" if supports_json else None)

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

        with patch("kitt.llm.providers.openai_chat.secure_urlopen", side_effect=socket.timeout("Timeout")):
            with self.assertRaises(ProviderTimeoutError):
                list(adapter.stream(req))

    def test_discovery_status_on_auth_failure_vs_success(self):
        adapter = OpenAIChatAdapter()

        # Auth failure (401)
        err_401 = urllib.error.HTTPError("http://test/v1/models", 401, "Unauthorized", {}, io.BytesIO(b'{"error":"invalid"}'))
        with patch("kitt.llm.providers.openai_chat.secure_urlopen", side_effect=err_401):
            res = adapter.list_models(base_url="https://api.openai.com", api_key="bad-key")
            self.assertEqual(res.status, ProviderDiscoveryStatus.AUTH_INVALID)
            self.assertEqual(len(res.models), 0)

        err_429 = urllib.error.HTTPError("http://test/v1/models", 429, "Rate Limited", {}, io.BytesIO(b'{"error":"rate_limit"}'))
        with patch("kitt.llm.providers.openai_chat.secure_urlopen", side_effect=err_429):
            res = adapter.list_models(base_url="https://api.openai.com", api_key="quota-key")
            self.assertEqual(res.status, ProviderDiscoveryStatus.RATE_LIMITED)

    def test_json_structured_final_is_decoded(self):
        from kitt.llm.agent_contract import parse_structured_result

        raw = '{"tasks":[{"id":"T01","status":"done"},{"id":"T02","status":"pending"}]}'
        result = parse_structured_result(raw)
        self.assertEqual(result["tasks"], [
            {"id": "T01", "status": "done"},
            {"id": "T02", "status": "pending"},
        ])



if __name__ == "__main__":
    unittest.main()
