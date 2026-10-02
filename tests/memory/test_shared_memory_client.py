from __future__ import annotations

import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from kitt_protocol import (
    Envelope,
    MEMORY_BASELINE_RESPONSE,
    MEMORY_GET_RESPONSE,
    MEMORY_SEARCH_RESPONSE,
    MEMORY_TIMELINE_RESPONSE,
)
from kitt.memory.shared_client import SharedMemoryClient, SharedMemoryUnavailable


class SharedClientTest(unittest.TestCase):
    def _server(self, response_builder):
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        host, port = server.getsockname()

        def serve():
            conn, _ = server.accept()
            with conn:
                data = b""
                while not data.endswith(b"\n"):
                    data += conn.recv(4096)
                frame = json.loads(data)
                response = response_builder(frame)
                conn.sendall(response.dumps().encode() + b"\n")
            server.close()

        threading.Thread(target=serve, daemon=True).start()
        return host, port

    def test_progressive_search_forwards_memory_budget(self):
        seen = {}

        def response(frame):
            request = frame["envelope"]
            self.assertEqual("memory.search.request", request["kind"])
            seen.update(request["payload"])
            return Envelope(
                kind=MEMORY_SEARCH_RESPONSE,
                correlation_id=request["id"],
                payload={
                    "recall_trace_id": "trace-search",
                    "hits": [
                        {
                            "id": "mem-1",
                            "snippet": "short rule",
                            "kind": "project_rule",
                            "scope": "workspace",
                            "sensitivity": "private",
                        }
                    ],
                    "consumed_tokens": 7,
                    "has_more": False,
                },
            )

        host, port = self._server(response)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            token = Path(tmp) / "token"
            token.write_text("a" * 64)
            client = SharedMemoryClient(f"{host}:{port}", token, 1.0)
            hits, trace_id = client.search(
                "ws",
                "rule",
                max_results=17,
                token_budget=333,
                scope_key="conv-1",
                as_of=1_700_000_000,
                exclude_ids=("mem-old",),
                include_context_hints=True,
            )
        self.assertEqual("trace-search", trace_id)
        self.assertEqual("mem-1", hits[0]["id"])
        self.assertEqual(17, seen["max_results"])
        self.assertEqual(333, seen["token_budget"])
        self.assertEqual("conv-1", seen["scope_key"])
        self.assertEqual(1_700_000_000, seen["as_of"])
        self.assertEqual(["mem-old"], seen["exclude_ids"])
        self.assertTrue(seen["include_context_hints"])
        self.assertFalse(seen["include_provenance"])

    def test_progressive_get_and_timeline_preserve_correlation(self):
        responses = [
            (
                "memory.get.request",
                MEMORY_GET_RESPONSE,
                {
                    "recall_trace_id": "trace-get",
                    "records": [
                        {
                            "record": {
                                "id": "mem-1",
                                "content": "full body",
                                "scope": "workspace",
                                "pinned": False,
                            },
                            "provenance": [{"uri": "kitt://source/1"}],
                        }
                    ],
                    "consumed_tokens": 3,
                    "truncated_ids": ["mem-2"],
                },
            ),
            (
                "memory.timeline.request",
                MEMORY_TIMELINE_RESPONSE,
                {
                    "recall_trace_id": "trace-timeline",
                    "hits": [{"id": "mem-1", "snippet": "full body"}],
                    "consumed_tokens": 3,
                    "has_more": False,
                },
            ),
        ]

        for expected_request, response_kind, payload in responses:
            def response(frame, expected_request=expected_request, response_kind=response_kind, payload=payload):
                request = frame["envelope"]
                self.assertEqual(expected_request, request["kind"])
                return Envelope(
                    kind=response_kind,
                    correlation_id=request["id"],
                    payload=payload,
                )

            host, port = self._server(response)
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                token = Path(tmp) / "token"
                token.write_text("a" * 64)
                client = SharedMemoryClient(f"{host}:{port}", token, 1.0)
                if expected_request == "memory.get.request":
                    records, trace_id, truncated = client.get(
                        "ws", ["mem-1", "mem-2"], token_budget=128
                    )
                    self.assertEqual("trace-get", trace_id)
                    self.assertEqual(["mem-2"], truncated)
                    self.assertEqual("full body", records[0]["record"]["content"])
                else:
                    hits, trace_id = client.timeline(
                        "ws", source_id="session-1", token_budget=128
                    )
                    self.assertEqual("trace-timeline", trace_id)
                    self.assertEqual("mem-1", hits[0]["id"])

    def test_baseline_supports_etag_reuse(self):
        seen = {}

        def response(frame):
            request = frame["envelope"]
            self.assertEqual("memory.baseline.request", request["kind"])
            seen.update(request["payload"])
            return Envelope(
                kind=MEMORY_BASELINE_RESPONSE,
                correlation_id=request["id"],
                payload={
                    "not_modified": True,
                    "etag": "etag-2",
                    "baseline_revision": 7,
                    "entries": [],
                    "estimated_tokens": 0,
                },
            )

        host, port = self._server(response)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            token = Path(tmp) / "token"
            token.write_text("a" * 64)
            client = SharedMemoryClient(f"{host}:{port}", token, 1.0)
            body = client.baseline("ws", max_tokens=512, if_none_match="etag-1")

        self.assertTrue(body["not_modified"])
        self.assertEqual("etag-2", body["etag"])
        self.assertEqual(512, seen["max_tokens"])
        self.assertEqual("etag-1", seen["if_none_match"])

    def test_legacy_recall_surface_is_not_exposed(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            token = Path(tmp) / "token"
            token.write_text("a" * 64)
            client = SharedMemoryClient("127.0.0.1:41827", token, 1.0)
            self.assertFalse(hasattr(client, "recall"))
            self.assertFalse(hasattr(client, "recall_with_trace"))

    def test_conversation_remember_requires_scope_key(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            token = Path(tmp) / "token"
            token.write_text("a" * 64)
            client = SharedMemoryClient("127.0.0.1:41827", token, 1.0)
            with self.assertRaisesRegex(ValueError, "requires scope_key"):
                client.remember("ws", "rule", scope="conversation")

    def test_correlation_mismatch_is_rejected(self):
        def response(_frame):
            return Envelope(
                kind=MEMORY_SEARCH_RESPONSE,
                correlation_id="wrong",
                payload={"hits": [], "recall_trace_id": "trace"},
            )

        host, port = self._server(response)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            token = Path(tmp) / "token"
            token.write_text("a" * 64)
            client = SharedMemoryClient(f"{host}:{port}", token, 1.0)
            with self.assertRaises(SharedMemoryUnavailable):
                client.search("ws", "rule")

    def test_remote_daemon_address_is_rejected_before_token_egress(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            token = Path(tmp) / "token"
            token.write_text("a" * 64)
            client = SharedMemoryClient("192.0.2.10:41827", token, 1.0)
            with self.assertRaisesRegex(SharedMemoryUnavailable, "loopback"):
                client._split_address()

    def test_bracketed_ipv6_loopback_is_supported(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            token = Path(tmp) / "token"
            token.write_text("a" * 64)
            client = SharedMemoryClient("[::1]:41827", token, 1.0)
            self.assertEqual(("::1", 41827), client._split_address())


if __name__ == "__main__":
    unittest.main()
