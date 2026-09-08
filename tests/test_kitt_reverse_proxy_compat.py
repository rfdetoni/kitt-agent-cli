import io
import json
import unittest
from unittest.mock import MagicMock, patch

from kitt.domain.entities import ModelProfile
from kitt.llm.client import LLMClient
from kitt.llm.domain import ProviderProtocolError
from kitt.tools.protocol import parse_tool_call
from kitt.llm.providers.base import LLMRequest
from kitt.llm.providers.kitt_reverse_proxy import (
    KittReverseProxyAdapter,
    extract_openai_tools,
    normalize_native_tool_messages,
    strip_legacy_tool_contract,
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
        tools = extract_openai_tools(f"Available host tool: {definitions}")
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

    def test_extracts_native_tools_from_existing_prompt_contract(self):
        prompt = (
            "Available host tools: "
            "[{'name': 'read_file', 'description': 'Read file', "
            "'args': {'path': 'relative file', 'start_line': 'int >=1'}}, "
            "{'name': 'search', 'description': 'Search repository', "
            "'args': {'pattern': 'literal text or regex', 'regex': 'bool, default false'}}]\n"
            "For a host tool, respond with exactly:"
        )
        tools = extract_openai_tools(prompt)
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

    def test_ordinary_messages_are_not_reinterpreted(self):
        source = [{"role": "user", "content": "hello"}]
        self.assertEqual(normalize_native_tool_messages(source), source)


    def test_strips_only_legacy_tool_contract(self):
        source = (
            "You are K.I.T.T.\n\n"
            "Tool Contract:\nAvailable host tools: [{'name': 'read_file'}]\n"
            "For a host tool, respond with exactly: <kitt-tool>...</kitt-tool>\n\n"
            "Memory:\nkeep this\n\nProject Guidelines:\nkeep that"
        )
        cleaned = strip_legacy_tool_contract(source)
        self.assertNotIn("Tool Contract:", cleaned)
        self.assertIn("Memory:\nkeep this", cleaned)
        self.assertIn("Project Guidelines:\nkeep that", cleaned)

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

        system_prompt = (
            "Tool Contract:\n"
            "Available host tools: [{'name': 'read_file', 'description': 'Read file', "
            "'args': {'path': 'relative file'}}]\n"
            "For a host tool, respond with exactly: <kitt-tool>...</kitt-tool>\n\n"
            "Memory:\nnone"
        )
        request = LLMRequest(
            model="chatgpt-web",
            messages=[{"role": "user", "content": "inspect"}],
            system_prompt=system_prompt,
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

        sent = json.loads(captured["request"].data.decode("utf-8"))
        self.assertEqual(sent["tool_choice"], "auto")
        self.assertFalse(sent["parallel_tool_calls"])
        self.assertEqual(sent["tools"][0]["function"]["name"], "read_file")
        self.assertNotIn("Tool Contract:", sent["messages"][0]["content"])
        headers = {key.lower(): value for key, value in captured["request"].header_items()}
        self.assertEqual(headers["x-kitt-session-id"], "abc123")
        self.assertEqual(headers["x-kitt-request-id"], "req123")



    def test_llm_client_uses_stable_session_per_conversation_and_unique_request_ids(self):
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
                    )),
                    "ok",
                )
                self.assertEqual(
                    "".join(client.chat_stream(
                        [{"role": "user", "content": "two"}],
                        session_key="conversation-a",
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
        self.assertRegex(first["X-Kitt-Session-Id"], r"^[a-f0-9]{32}$")



if __name__ == "__main__":
    unittest.main()
