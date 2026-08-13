import tempfile
import unittest
from pathlib import Path

from kitt.tools.spec import ToolSpec
from kitt.security.egress import EgressPolicy
from kitt.security.sensitive_data import SensitiveDataScanner
from kitt.security.path_policy import PathPolicy
from kitt.security.network_policy import NetworkPolicy

class TestSecurityAndPolicies(unittest.TestCase):
    def test_tool_spec_validation(self):
        spec = ToolSpec(
            name="test_tool",
            description="A test tool",
            input_schema={"required": ["path"]},
            output_schema={},
            effects=("read",)
        )
        ok, err = spec.validate_args({"path": "app.py"})
        self.assertTrue(ok)
        ok_fail, err_fail = spec.validate_args({})
        self.assertFalse(ok_fail)
        self.assertIn("Missing required parameter", err_fail)

    def test_egress_policy_and_manifest(self):
        policy = EgressPolicy(mode="local_only")
        ok, manifest, reason = policy.evaluate_egress("127.0.0.1", is_local=True, provider="ollama", model="qwen", workspace_id="ws", bytes_out=100, estimated_tokens=25)
        self.assertTrue(ok)
        self.assertIsNotNone(manifest)

        ok_remote, _, err_remote = policy.evaluate_egress("api.openai.com", is_local=False, provider="openai", model="gpt-4", workspace_id="ws", bytes_out=100, estimated_tokens=25)
        self.assertFalse(ok_remote)

    def test_sensitive_data_scanner(self):
        raw = "My API key is sk-proj-12345678901234567890 and AWS key AKIAIOSFODNN7EXAMPLE"
        res = SensitiveDataScanner.scan_and_redact(raw)
        self.assertTrue(res.has_sensitive)
        self.assertNotIn("sk-proj-12345678901234567890", res.clean_text)
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", res.clean_text)
        self.assertIn("[REDACTED", res.clean_text)

    def test_path_policy(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            policy = PathPolicy(tmpdir)
            ok, target, err = policy.validate_path("app.py")
            self.assertTrue(ok)

            ok_ssh, _, err_ssh = policy.validate_path(".ssh/id_rsa")
            self.assertFalse(ok_ssh)

            ok_env, _, err_env = policy.validate_path(".env")
            self.assertFalse(ok_env)

            ok_esc, _, err_esc = policy.validate_path("../../etc/passwd")
            self.assertFalse(ok_esc)

    def test_network_policy_ssrf(self):
        ok_loop, _ = NetworkPolicy.validate_url("http://127.0.0.1:11434/api/generate")
        self.assertTrue(ok_loop)

        ok_remote_http, err_http = NetworkPolicy.validate_url("http://api.openai.com/v1/chat")
        self.assertFalse(ok_remote_http)
        self.assertIn("Insecure HTTP forbidden", err_http)

        ok_meta, err_meta = NetworkPolicy.validate_url("http://169.254.169.254/latest/meta-data")
        self.assertFalse(ok_meta)
        self.assertIn("metadata/blocked IP", err_meta)

if __name__ == "__main__":
    unittest.main()
