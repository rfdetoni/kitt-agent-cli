import io
import json
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from kitt.children.worker import _bind_child_proxy_session
from kitt.domain.entities import ModelProfile
from kitt.llm.client import LLMClient
from kitt.llm.kitt_proxy_capabilities import KittProxyCapabilities
from kitt.llm.domain import ProviderProtocolError
from kitt.tools.protocol import extract_tool_reasoning_summary, parse_tool_call
from kitt.llm.providers.base import LLMRequest
from kitt.llm.providers.kitt_reverse_proxy import (
    KittReverseProxyAdapter,
    normalize_native_tool_messages,
    openai_tools_from_definitions,
)


class TestKittReverseProxyCompatibility(unittest.TestCase):
    def test_canonical_arguments_are_never_reinterpreted_as_nested_calls(self):
        for content in [
            "read_file('secret.txt')",
            '<write_file path="other.txt">wrong</write_file>',
            '<think>literal source</think></kitt-tool>',
        ]:
            arguments = {"path": "example.txt", "content": content}
            envelope = '<kitt-tool>' + json.dumps({
                "id": "call_example", "name": "write_file", "arguments": arguments,
            }) + '</kitt-tool>'
            self.assertEqual(parse_tool_call(envelope), ("write_file", arguments))
            restored = normalize_native_tool_messages([
                {"role": "assistant", "content": envelope},
                {"role": "user", "content": "write_file result from the host: ok"},
            ])
            self.assertEqual(json.loads(restored[0]["tool_calls"][0]["function"]["arguments"]), arguments)

    def test_canonical_empty_arguments_and_custom_name_are_preserved(self):
        self.assertEqual(parse_tool_call(
            '<kitt-tool>{"name":"MCP.Read","arguments":{}}</kitt-tool>'
        ), ("MCP.Read", {}))
        with self.assertRaises(ValueError):
            parse_tool_call('<kitt-tool>{"name":"write_file","arguments":{}</kitt-tool>')

    def test_runtime_schema_retains_operation_catalog(self):
        from kitt.tools.registry import ToolRegistry
        from kitt.runtime.safe_runtime import OPERATION_SPECS
        registry = object.__new__(ToolRegistry)
        registry._custom_tools = {}
        definitions = registry.get_tool_definitions(["kitt_runtime"])
        tools = openai_tools_from_definitions(definitions)
        schema = tools[0]["function"]["parameters"]
        self.assertEqual(schema["properties"]["operation"]["enum"], list(OPERATION_SPECS))
        self.assertIn("operation", schema["required"])
        self.assertEqual(schema["properties"]["arguments"]["type"], "object")

    def test_stream_errors_and_truncated_calls_fail_before_execution(self):
        prompt = "Available host tools: [{'name': 'read_file', 'args': {'path': 'string'}}]"
        tool_event = {"choices": [{"delta": {"tool_calls": [{
            "index": 0, "id": "call_test", "function": {
                "name": "read_file", "arguments": '{"path":"README.md"}',
            },
        }]}}]}
        encoded = f"data: {json.dumps(tool_event)}\n\n".encode()
        for raw in [encoded, b'data: {"error":{"code":"upstream_error"}}\n\ndata: [DONE]\n', b'data: broken\n']:
            with self.subTest(raw=raw), patch(
                'kitt.llm.providers.kitt_reverse_proxy.secure_urlopen', return_value=io.BytesIO(raw)
            ):
                with self.assertRaises(ProviderProtocolError):
                    list(KittReverseProxyAdapter().stream(LLMRequest(
                        model='chatgpt-web', messages=[{'role': 'user', 'content': 'inspect'}],
                        system_prompt=prompt,
                    )))

    def test_oversized_sse_line_is_read_with_a_bound(self):
        class BoundedResponse(io.BytesIO):
            def readline(self, size=-1):
                self_test.assertGreater(size, 0)
                self_test.assertLessEqual(size, 4 * 1024 * 1024 + 1)
                return super().readline(size)
        self_test = self
        with patch('kitt.llm.providers.kitt_reverse_proxy.secure_urlopen',
                   return_value=BoundedResponse(b'x' * (4 * 1024 * 1024 + 2))):
            with self.assertRaises(ProviderProtocolError):
                list(KittReverseProxyAdapter().stream(LLMRequest(model='fixture', messages=[])))

    def test_converts_structural_tool_definitions_without_prompt_parsing(self):
        tools = openai_tools_from_definitions([
            {
                "name": "read_file",
                "description": "Read file",
                "args": {"path": "relative file", "start_line": "int >=1"},
            },
            {
                "name": "search",
                "description": "Search repository",
                "args": {"pattern": "literal text or regex", "regex": "bool, default false"},
            },
        ])
        self.assertEqual(
            [tool["function"]["name"] for tool in tools],
            ["read_file", "search"],
        )
        schema = tools[0]["function"]["parameters"]
        self.assertEqual(schema["properties"]["start_line"]["type"], "integer")
        self.assertIn("path", schema["required"])

    def test_native_tool_call_and_result_round_trip(self):
        envelope = (
            '<kitt-tool>{"id":"call_abc123","name":"read_file",'
            '"arguments":{"path":"README.md"}}</kitt-tool>'
        )
        source = [
            {"role": "user", "content": "inspect"},
            {"role": "assistant", "content": envelope},
            {"role": "user", "content": "read_file result from the host:\nhello"},
        ]
        normalized = normalize_native_tool_messages(source)
        self.assertEqual(normalized[1]["role"], "assistant")
        self.assertEqual(
            normalized[1]["tool_calls"][0]["function"]["name"], "read_file"
        )
        self.assertEqual(normalized[2]["role"], "tool")
        self.assertEqual(normalized[2]["tool_call_id"], "call_abc123")
        self.assertEqual(normalized[2]["name"], "read_file")
    
    def test_native_tool_round_trip_preserves_reasoning_summary(self):
        envelope = (
            '<kitt-tool>{"id":"call_summary","name":"read_file",'
            '"arguments":{"path":"README.md"},'
            '"reasoning_summary":"Vou inspecionar o README antes de alterar o projeto."}'
            '</kitt-tool>'
        )
        normalized = normalize_native_tool_messages([
            {"role": "assistant", "content": envelope},
            {"role": "user", "content": "read_file result from the host:\nok"},
        ])

        self.assertEqual(
            normalized[0]["content"],
            "Vou inspecionar o README antes de alterar o projeto.",
        )
        self.assertEqual(
            extract_tool_reasoning_summary(envelope),
            "Vou inspecionar o README antes de alterar o projeto.",
        )


    def test_ordinary_messages_are_not_reinterpreted(self):
        source = [{"role": "user", "content": "hello"}]
        self.assertEqual(normalize_native_tool_messages(source), source)

    def test_structural_tool_definitions_survive_compact_prompt(self):
        class FakeResponse:
            headers = {}

            def __init__(self):
                self._stream = io.BytesIO(b"data: [DONE]" + bytes([10]))

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def readline(self, size=-1):
                return self._stream.readline(size)

        captured = {}

        def fake_open(request, timeout):
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        request = LLMRequest(
            model="gemini-web",
            messages=[{"role": "user", "content": "inspect"}],
            system_prompt="[KITT EXECUTION SLICE: DISCOVERY]\\nInspect first.",
            tool_definitions=[{
                "name": "kitt_runtime",
                "description": "Workspace runtime",
                "args": {
                    "operation": {
                        "type": "string",
                        "enum": ["repo.list", "repo.read", "repo.write_file"],
                    },
                    "arguments": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                },
            }],
            base_url="http://127.0.0.1:3000",
            extra_headers={"X-Kitt-Agent-Contract": "v1"},
        )

        with patch(
            "kitt.llm.providers.kitt_reverse_proxy.secure_urlopen",
            side_effect=fake_open,
        ):
            list(KittReverseProxyAdapter().stream(request))

        payload = captured["payload"]
        self.assertEqual(payload["tools"][0]["function"]["name"], "kitt_runtime")
        self.assertEqual(
            payload["tools"][0]["function"]["parameters"]["properties"]["operation"]["enum"],
            ["repo.list", "repo.read", "repo.write_file"],
        )
        self.assertNotIn("Tool Contract:", payload["messages"][0]["content"])

    def test_stream_reconstructs_native_tool_call_and_preserves_headers(self):
        class FakeResponse:
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def readline(self, size=-1):
                if not hasattr(self, '_stream'):
                    self._stream = io.BytesIO(b''.join(self))
                return self._stream.readline(size)

            def __iter__(self):
                payloads = [
                    {
                        "choices": [{
                            "delta": {
                                "content": "Vou inspecionar o README antes de continuar.",
                                "tool_calls": [{
                                    "index": 0,
                                    "id": "call_abc123",
                                    "type": "function",
                                    "function": {
                                        "name": "read_",
                                        "arguments": '{"path":"README'
                                    }
                                }]
                            }
                        }]
                    },
                    {
                        "choices": [{
                            "delta": {
                                "tool_calls": [{
                                    "index": 0,
                                    "function": {
                                        "name": "file",
                                        "arguments": '.md"}'
                                    }
                                }]
                            }
                        }]
                    },
                ]
                for payload in payloads:
                    yield f"data: {json.dumps(payload)}\n".encode()
                yield b"data: [DONE]\n"

        captured = {}

        def fake_open(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeResponse()

        request = LLMRequest(
            model="chatgpt-web",
            messages=[{"role": "user", "content": "inspect"}],
            system_prompt="Memory:\nnone",
            tool_definitions=[{
                "name": "read_file",
                "description": "Read file",
                "args": {"path": "relative file"},
            }],
            base_url="http://127.0.0.1:3000",
            extra_headers={
                "X-Kitt-Session-Id": "abc123",
                "X-Kitt-Request-Id": "req123",
            },
        )

        with patch(
            "kitt.llm.providers.kitt_reverse_proxy.secure_urlopen",
            side_effect=fake_open,
        ):
            output = "".join(KittReverseProxyAdapter().stream(request))

        self.assertIn('"id":"call_abc123"', output)
        self.assertIn('"name":"read_file"', output)
        self.assertIn('"path":"README.md"', output)
        self.assertIn(
            '"reasoning_summary":"Vou inspecionar o README antes de continuar."',
            output,
        )

        sent = json.loads(captured["request"].data.decode("utf-8"))
        self.assertEqual(sent["tool_choice"], "auto")
        self.assertFalse(sent["parallel_tool_calls"])
        self.assertEqual(sent["tools"][0]["function"]["name"], "read_file")
        self.assertEqual(sent["messages"][0]["content"], "Memory:\nnone")
        headers = {key.lower(): value for key, value in captured["request"].header_items()}
        self.assertEqual(headers["x-kitt-session-id"], "abc123")
        self.assertEqual(headers["x-kitt-request-id"], "req123")

    def test_retained_children_keep_stable_proxy_sessions_without_sharing(self):
        from kitt.core.turn_processor import TurnProcessor

        profile = ModelProfile(
            backend="kitt-reverse-proxy",
            protocol="kitt-reverse-proxy",
            model="chatgpt-web",
            base_url="http://127.0.0.1:3000",
        )

        def provider_key(child_id, conversation_id):
            processor = object.__new__(TurnProcessor)
            runtime = MagicMock()
            runtime.workspace_id = "workspace-a"
            runtime.processor = processor
            scope = _bind_child_proxy_session(runtime, {
                "child_id": child_id,
                "runtime_conversation_id": conversation_id,
            })
            return scope, processor._provider_session_key(profile, conversation_id)

        scope_a1, key_a1 = provider_key("child-a", "childconv_child-a")
        scope_a2, key_a2 = provider_key("child-a", "childconv_child-a")
        scope_b, key_b = provider_key("child-b", "childconv_child-b")

        self.assertEqual(scope_a1, scope_a2)
        self.assertEqual(key_a1, key_a2)
        self.assertNotEqual(scope_a1, scope_b)
        self.assertNotEqual(key_a1, key_b)
        self.assertIn("retained-child:workspace-a:child-a", key_a1)
        self.assertIn("conversation:childconv_child-a", key_a1)

    @patch("kitt.llm.client.discover_kitt_proxy_capabilities", return_value=KittProxyCapabilities())
    def test_llm_client_uses_stable_session_per_conversation_and_unique_request_ids(self, _discover):
        captured = []

        class CaptureAdapter:
            def stream(self, request):
                captured.append(request)
                yield "ok"

        registry = MagicMock()
        registry.auth_service = MagicMock()
        registry.endpoint_policy = MagicMock()
        registry.get_adapter_for_protocol.return_value = CaptureAdapter()

        profile = ModelProfile(
            backend="kitt-reverse-proxy",
            protocol="kitt-reverse-proxy",
            model="chatgpt-web",
            base_url="http://127.0.0.1:3000",
            supports_tools=True,
        )
        with patch("kitt.llm.client.resolve_endpoint_credential", return_value=None):
            with LLMClient(
                profile,
                registry=registry,
                auth_service=MagicMock(),
                endpoint_policy=MagicMock(),
            ) as client:
                self.assertEqual(
                    "".join(client.chat_stream(
                        [{"role": "user", "content": "one"}],
                        session_key="conversation-a",
                        reasoning_effort=80,
                    )),
                    "ok",
                )
                self.assertEqual(
                    "".join(client.chat_stream(
                        [{"role": "user", "content": "two"}],
                        session_key="conversation-a",
                        reasoning_effort=20,
                    )),
                    "ok",
                )
                self.assertEqual(
                    "".join(client.chat_stream(
                        [{"role": "user", "content": "other"}],
                        session_key="conversation-b",
                    )),
                    "ok",
                )

        first = captured[0].extra_headers
        second = captured[1].extra_headers
        third = captured[2].extra_headers
        self.assertEqual(first["X-Kitt-Session-Id"], second["X-Kitt-Session-Id"])
        self.assertNotEqual(first["X-Kitt-Session-Id"], third["X-Kitt-Session-Id"])
        self.assertNotEqual(first["X-Kitt-Request-Id"], second["X-Kitt-Request-Id"])
        self.assertNotIn("X-Kitt-Reasoning-Effort", first)
        self.assertNotIn("X-Kitt-Reasoning-Effort", second)
        self.assertNotIn("X-Kitt-Reasoning-Effort", third)
        self.assertRegex(first["X-Kitt-Session-Id"], r"^[a-f0-9]{32}$")

    def test_stream_retries_without_reasoning_effort_on_unavailable_error(self):
        adapter = KittReverseProxyAdapter()
        calls = []

        class FakeHTTPError(urllib.error.HTTPError):
            def __init__(self):
                super().__init__("http://127.0.0.1:3000/v1/chat/completions", 400, "Bad Request", {}, None)
                self._fp = io.BytesIO(
                    b'{"error":{"message":"O nivel de reasoning solicitado nao esta disponivel: medium.","code":"reasoning_level_unavailable"}}'
                )

            def read(self, *args):
                return self._fp.read(*args)

        class FakeSuccessResponse:
            def __init__(self):
                self._lines = [
                    b'data: {"choices":[{"delta":{"content":"ok without reasoning"}}]}\n',
                    b"data: [DONE]\n",
                ]
                self._idx = 0

            def readline(self, *args):
                if self._idx < len(self._lines):
                    line = self._lines[self._idx]
                    self._idx += 1
                    return line
                return b""

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_urlopen(req, timeout=300):
            calls.append(req)
            if req.has_header("X-kitt-reasoning-effort"):
                raise FakeHTTPError()
            return FakeSuccessResponse()

        request = LLMRequest(
            model="chatgpt-web",
            messages=[{"role": "user", "content": "hello"}],
            tool_definitions=[{
                "name": "kitt_runtime",
                "description": "Workspace runtime",
                "args": {
                    "operation": {
                        "type": "string",
                        "enum": ["repo.list", "repo.read"],
                    },
                    "arguments": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                },
            }],
            extra_headers={"X-Kitt-Reasoning-Effort": "50"},
        )

        with patch("kitt.llm.providers.kitt_reverse_proxy.secure_urlopen", side_effect=fake_urlopen):
            chunks = list(adapter.stream(request))

        self.assertEqual("".join(chunks), "ok without reasoning")
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[0].has_header("X-kitt-reasoning-effort"))
        self.assertFalse(calls[1].has_header("X-kitt-reasoning-effort"))
        for call in calls:
            payload = json.loads(call.data.decode("utf-8"))
            self.assertEqual(
                payload["tools"][0]["function"]["name"],
                "kitt_runtime",
            )

    def test_tool_retry_preserves_structural_tool_definitions(self):
        adapter = KittReverseProxyAdapter()
        calls = []

        class FakeToolHTTPError(urllib.error.HTTPError):
            def __init__(self):
                super().__init__(
                    "http://127.0.0.1:3000/v1/chat/completions",
                    400,
                    "Bad Request",
                    {},
                    None,
                )
                self._fp = io.BytesIO(
                    b'{"error":{"message":"invalid tool payload","code":"tool_parse_failed"}}'
                )

            def read(self, *args):
                return self._fp.read(*args)

        class FakeSuccessResponse:
            def __init__(self):
                self._lines = [
                    b'data: {"choices":[{"delta":{"content":"ok after retry"}}]}' + bytes([10]),
                    b"data: [DONE]" + bytes([10]),
                ]
                self._idx = 0

            def readline(self, *args):
                if self._idx < len(self._lines):
                    line = self._lines[self._idx]
                    self._idx += 1
                    return line
                return b""

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_urlopen(req, timeout=300):
            calls.append(req)
            if len(calls) == 1:
                raise FakeToolHTTPError()
            return FakeSuccessResponse()

        request = LLMRequest(
            model="gemini-web",
            messages=[{"role": "user", "content": "inspect"}],
            system_prompt="[KITT EXECUTION SLICE: DISCOVERY]\nInspect first.",
            tool_definitions=[{
                "name": "kitt_runtime",
                "description": "Workspace runtime",
                "args": {
                    "operation": {
                        "type": "string",
                        "enum": ["repo.list", "repo.read"],
                    },
                    "arguments": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                },
            }],
        )

        with patch(
            "kitt.llm.providers.kitt_reverse_proxy.secure_urlopen",
            side_effect=fake_urlopen,
        ):
            chunks = list(adapter.stream(request))

        self.assertEqual("".join(chunks), "ok after retry")
        self.assertEqual(len(calls), 2)
        first_payload = json.loads(calls[0].data.decode("utf-8"))
        retry_payload = json.loads(calls[1].data.decode("utf-8"))
        self.assertEqual(
            first_payload["tools"][0]["function"]["name"],
            "kitt_runtime",
        )
        self.assertEqual(
            retry_payload["tools"][0]["function"]["name"],
            "kitt_runtime",
        )
        self.assertIn(
            "KITT TOOL RETRY",
            retry_payload["messages"][-1]["content"],
        )

    def test_usage_callback_receives_proxy_usage_metadata(self):
        observed = []

        class FakeResponse:
            def __init__(self):
                usage = {
                    "prompt_tokens": 17,
                    "completion_tokens": 5,
                    "total_tokens": 22,
                    "kitt_estimated": True,
                }
                self._lines = [
                    (
                        "data: "
                        + json.dumps({
                            "choices": [{"delta": {"content": "ok"}}],
                            "usage": usage,
                        })
                        + "\n"
                    ).encode(),
                    b"data: [DONE]\n",
                ]
                self._index = 0

            def readline(self, *_args):
                if self._index >= len(self._lines):
                    return b""
                line = self._lines[self._index]
                self._index += 1
                return line

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        request = LLMRequest(
            model="chatgpt-web",
            messages=[{"role": "user", "content": "hello"}],
            usage_callback=lambda usage: observed.append(dict(usage)),
        )
        with patch(
            "kitt.llm.providers.kitt_reverse_proxy.secure_urlopen",
            return_value=FakeResponse(),
        ):
            self.assertEqual(
                "".join(KittReverseProxyAdapter().stream(request)),
                "ok",
            )

        self.assertEqual(len(observed), 1)
        self.assertEqual(observed[0]["prompt_tokens"], 17)
        self.assertEqual(observed[0]["completion_tokens"], 5)
        self.assertTrue(observed[0]["kitt_estimated"])

    def test_tool_retry_preserves_typed_context_envelope(self):
        adapter = KittReverseProxyAdapter()
        calls = []

        class FakeToolHTTPError(urllib.error.HTTPError):
            def __init__(self):
                super().__init__(
                    "http://127.0.0.1:3000/v1/chat/completions",
                    400,
                    "Bad Request",
                    {},
                    None,
                )
                self._fp = io.BytesIO(
                    b'{"error":{"message":"invalid tool payload","code":"tool_parse_failed"}}'
                )

            def read(self, *args):
                return self._fp.read(*args)

        class FakeSuccessResponse:
            def __init__(self):
                self._lines = [
                    b'data: {"choices":[{"delta":{"content":"ok"}}]}\n',
                    b"data: [DONE]\n",
                ]
                self._index = 0

            def readline(self, *_args):
                if self._index >= len(self._lines):
                    return b""
                line = self._lines[self._index]
                self._index += 1
                return line

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        def fake_open(req, timeout=300):
            calls.append(req)
            if len(calls) == 1:
                raise FakeToolHTTPError()
            return FakeSuccessResponse()

        envelope = {
            "schema_version": 1,
            "epoch": "ctx-test",
            "segments": [],
        }
        request = LLMRequest(
            model="chatgpt-web",
            messages=[{"role": "user", "content": "inspect"}],
            context_envelope=envelope,
            tool_definitions=[{
                "name": "kitt_runtime",
                "args": {
                    "operation": {"type": "string", "enum": ["repo.read"]},
                    "arguments": {"type": "object"},
                },
            }],
        )

        with patch(
            "kitt.llm.providers.kitt_reverse_proxy.secure_urlopen",
            side_effect=fake_open,
        ):
            self.assertEqual(
                "".join(adapter.stream(request)),
                "ok",
            )

        self.assertEqual(len(calls), 2)
        for call in calls:
            body = json.loads(call.data.decode("utf-8"))
            self.assertEqual(body["kitt_context"], envelope)

    def test_read_error_body_caches_on_repeated_reads(self):
        from kitt.llm.http_security import read_error_body

        class SingleReadError(urllib.error.HTTPError):
            def __init__(self):
                super().__init__("http://example.test", 400, "Bad Request", {}, None)
                self._fp = io.BytesIO(b'{"error":"specific detail"}')

            def read(self, *args):
                return self._fp.read(*args)

        err = SingleReadError()
        first_read = read_error_body(err)
        second_read = read_error_body(err)
        self.assertEqual(first_read, '{"error":"specific detail"}')
        self.assertEqual(second_read, '{"error":"specific detail"}')


if __name__ == "__main__":
    unittest.main()
