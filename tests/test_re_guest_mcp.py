"""Security regressions for host-owned guest status MCP adapter."""
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

from kitt import re_guest_mcp


class GuestMcpTests(unittest.TestCase):
    def rpc(self, name, arguments=None):
        return {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                "params": {"name": name, "arguments": arguments if arguments is not None else {}}}

    def test_only_explicit_readonly_tools(self):
        names = {x["name"] for x in re_guest_mcp.TOOLS}
        self.assertEqual(names, {"guests_status", "windows_oracle_doctor"})
        response = re_guest_mcp.respond({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual({x["name"] for x in response["result"]["tools"]}, names)
        for name in ("guest_exec", "guest_write", "docker_run", "probe_run"):
            self.assertEqual(re_guest_mcp.respond(self.rpc(name))["error"]["code"], -32602)

    def test_status_does_not_leak_paths_or_ids(self):
        observation = {
            "status": "RUNNING",
            "root": "/home/hidden",
            "guests": {"linux": {
                "status": "running", "environment": "linux-toolbox",
                "backend": "docker", "managed": True, "workspace": "/secret/foo",
                "container_id": "f" * 64},
                "windows": {"status": "created", "console": "http://127.0.0.1:8006"}},
            "ssh": "/home/user/.ssh/operator",
            "oracle_ready": True,
        }
        with patch("kitt.re_lab.status", return_value=observation) as query:
            response = re_guest_mcp.respond(self.rpc("guests_status"))
        query.assert_called_once_with(state_root=None)
        result = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(result["guests"]["linux"]["status"], "running")
        self.assertNotIn("workspace", str(result))
        self.assertNotIn("container_id", str(result))
        self.assertNotIn(".ssh", str(result))
        self.assertFalse(result["oracle_ready"])
        self.assertFalse(result["guest_attested"])

    def test_oracle_doctor_requires_digest_and_only_returns_handshake(self):
        valid = "a" * 64
        with patch("kitt.re_oracle.doctor", return_value={
            "oracle_ready": True, "status": "READY", "guest": {
                "platform": "Windows", "architecture": "AMD64", "manifest_sha256": valid,
                "private_output": "secret"}, "credentials": "never"}) as doctor:
            bad = re_guest_mcp.respond(self.rpc("windows_oracle_doctor", {"manifest_sha256": "wrong"}))
            self.assertEqual(bad["error"]["code"], -32602)
            self.assertEqual(doctor.call_count, 0)
            ok = re_guest_mcp.respond(self.rpc("windows_oracle_doctor", {"manifest_sha256": valid}))
        self.assertFalse(ok["result"]["isError"])
        info = json.loads(ok["result"]["content"][0]["text"])
        self.assertTrue(info["oracle_ready"])
        self.assertFalse(info["guest_attested"])
        self.assertNotIn("credentials", str(info))
        self.assertNotIn("private_output", str(info))
        doctor.assert_called_once_with(manifest_sha256=valid)

    def test_stdio_handshake_and_denied_guest_commands(self):
        cmd = [sys.executable, "-m", "kitt.re_guest_mcp", "--role", "operator"]
        payloads = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            self.rpc("guest_exec", {"argv": ["sh", "-c", "id"]}),
        ]
        result = subprocess.run(cmd, input="".join(json.dumps(x) + "\n" for x in payloads),
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        replies = [json.loads(s) for s in result.stdout.splitlines()]
        self.assertEqual(len(replies), 3)
        self.assertEqual(replies[0]["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(replies[-1]["error"]["code"], -32602)


if __name__ == "__main__":
    unittest.main()
