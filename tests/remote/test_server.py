from __future__ import annotations

import http.client
import json
import threading
import unittest
from http.cookies import SimpleCookie
from pathlib import Path
from tempfile import TemporaryDirectory

from kitt.remote.server import RemoteServer, RemoteServerConfig


class FakeGateway:
    def __init__(self):
        self.created = []

    def status(self):
        return {"status": "ok", "workspace_root": "/tmp/work", "snapshot": {}}

    def extensions(self):
        return {"status": "ok", "plugins": [], "mcp": []}

    def list_sessions(self):
        return {"status": "ok", "sessions": [{"id": "s1", "title": "Main", "status": "ACTIVE"}], "active_session_id": "s1"}

    def create_session(self, title):
        self.created.append(title)
        return {"status": "ok", "session_id": "s2"}

    def get_session(self, session_id):
        return {"status": "ok", "conversation": {"id": session_id, "title": "Main"}, "messages": [], "approvals": [], "last_sequence": 0}

    def approvals(self, session_id=None):
        return {"status": "ok", "approvals": []}

    def send_input(self, session_id, text, mode="auto"):
        return {"status": "ok", "session_id": session_id, "turn_id": "t1"}

    def cancel_turn(self, session_id, turn_id):
        return {"status": "ok"}

    def approve(self, approval_id, session_id=None):
        return {"status": "ok", "decision": "approved"}

    def deny(self, approval_id, session_id=None):
        return {"status": "ok", "decision": "denied"}

    def artifacts(self, session_id):
        return {"status": "ok", "artifacts": [{"id": "art_1", "artifact_type": "text", "summary": "demo", "size_bytes": 4}]}

    def read_artifact(self, session_id, artifact_id, offset=0):
        return {"status": "ok", "artifact": {"id": artifact_id}, "content": "demo", "has_more": False, "bytes_returned": 4, "total_bytes": 4}

    def workspace_diff(self):
        return {"status": "ok", "available": True, "content": "diff --git a/a b/a", "truncated": False}

    def stream_events(self, session_id, last_sequence, emit, heartbeat, stop):
        heartbeat()
        stop.set()


class RemoteServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.gateway = FakeGateway()
        self.server = RemoteServer(
            RemoteServerConfig(workspace_root=self.tmp.name, host="127.0.0.1", port=0),
            gateway=self.gateway,
        )
        self.server.start()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.server.address

    def tearDown(self):
        self.server.stop()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=3)
        payload = None if body is None else json.dumps(body)
        hdrs = dict(headers or {})
        if body is not None:
            hdrs["Content-Type"] = "application/json"
        conn.request(method, path, body=payload, headers=hdrs)
        response = conn.getresponse()
        raw = response.read()
        result = (response.status, dict(response.getheaders()), raw)
        conn.close()
        return result

    def pair(self):
        status, headers, raw = self.request("POST", "/api/pair", {"code": self.server.auth.pairing_code})
        self.assertEqual(status, 200)
        data = json.loads(raw)
        cookie = SimpleCookie(); cookie.load(headers["Set-Cookie"])
        token_cookie = f"kitt_remote_session={cookie['kitt_remote_session'].value}"
        return token_cookie, data["csrf"]

    def test_public_bind_address_is_rejected(self):
        with self.assertRaises(ValueError):
            RemoteServerConfig(workspace_root=self.tmp.name, host="8.8.8.8", port=7337).validated()

    def test_static_assets_and_security_headers(self):
        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"K.I.T.T.", body)
        self.assertIn("default-src 'self'", headers.get("Content-Security-Policy", ""))
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")

    def test_pairing_cookie_and_csrf_protect_mutations(self):
        cookie, csrf = self.pair()
        status, _, raw = self.request("GET", "/api/sessions", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["active_session_id"], "s1")

        status, _, _ = self.request("POST", "/api/sessions", {"title": "No CSRF"}, {"Cookie": cookie})
        self.assertEqual(status, 403)
        status, _, raw = self.request(
            "POST", "/api/sessions", {"title": "Remote Session"},
            {"Cookie": cookie, "X-KITT-CSRF": csrf},
        )
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(raw)["session_id"], "s2")
        self.assertEqual(self.gateway.created, ["Remote Session"])

    def test_head_does_not_open_sse(self):
        cookie, _ = self.pair()
        status, _, _ = self.request("HEAD", "/api/sessions/s1/events", headers={"Cookie": cookie})
        self.assertEqual(status, 405)

    def test_diff_and_artifacts_are_authenticated_read_only_views(self):
        cookie, _ = self.pair()
        status, _, raw = self.request("GET", "/api/diff", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(raw)["available"])
        status, _, raw = self.request("GET", "/api/artifacts?session_id=s1", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["artifacts"][0]["id"], "art_1")
        status, _, raw = self.request("GET", "/api/artifacts/art_1?session_id=s1", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["content"], "demo")

    def test_cross_origin_is_rejected(self):
        status, _, _ = self.request(
            "POST", "/api/pair", {"code": self.server.auth.pairing_code},
            {"Origin": "http://evil.example"},
        )
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
