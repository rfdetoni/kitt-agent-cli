from __future__ import annotations

import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from kitt_protocol import Envelope, MEMORY_RECALL_RESPONSE
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

    def test_recall_protocol_v1(self):
        def response(frame):
            request = frame["envelope"]
            self.assertEqual("memory.recall.request", request["kind"])
            self.assertIsNone(request["payload"]["scope_key"])
            self.assertIsNone(request["payload"]["as_of"])
            return Envelope(
                kind=MEMORY_RECALL_RESPONSE,
                correlation_id=request["id"],
                payload={"records": [{"content": "rule", "pinned": True}]},
            )

        host, port = self._server(response)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            token = Path(tmp) / "token"
            token.write_text("a" * 64)
            client = SharedMemoryClient(f"{host}:{port}", token, 1.0)
            self.assertEqual("rule", client.recall("ws", "rule")[0]["content"])


    def test_scoped_point_in_time_recall_and_zero_limit_are_forwarded(self):
        seen = {}

        def response(frame):
            request = frame["envelope"]
            seen.update(request["payload"])
            return Envelope(
                kind=MEMORY_RECALL_RESPONSE,
                correlation_id=request["id"],
                payload={"records": []},
            )

        host, port = self._server(response)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            token = Path(tmp) / "token"
            token.write_text("a" * 64)
            client = SharedMemoryClient(f"{host}:{port}", token, 1.0)
            self.assertEqual(
                [],
                client.recall(
                    "ws",
                    "historical rule",
                    limit=0,
                    scope_key="conversation-42",
                    as_of=1_700_000_000,
                ),
            )

        self.assertEqual("conversation-42", seen["scope_key"])
        self.assertEqual(1_700_000_000, seen["as_of"])
        self.assertEqual(0, seen["limit"])

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
                kind=MEMORY_RECALL_RESPONSE,
                correlation_id="wrong",
                payload={"records": []},
            )

        host, port = self._server(response)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            token = Path(tmp) / "token"
            token.write_text("a" * 64)
            client = SharedMemoryClient(f"{host}:{port}", token, 1.0)
            with self.assertRaises(SharedMemoryUnavailable):
                client.recall("ws", "rule")

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
