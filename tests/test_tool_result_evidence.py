from __future__ import annotations

import unittest

from kitt.core.turn_processor import TurnProcessor
from kitt.core.turn_tool_loop import _format_host_tool_result, _host_tool_prefix
from kitt.domain.entities import ModelProfile


class ToolResultEvidenceRegressionTests(unittest.TestCase):
    def test_reverse_proxy_keeps_current_tool_observation_when_local_budget_is_full(self):
        processor = TurnProcessor.__new__(TurnProcessor)
        profile = ModelProfile(
            backend="kitt-reverse-proxy",
            protocol="kitt-reverse-proxy",
            model="gemini-web",
            context_window=8192,
            max_output_tokens=2048,
        )
        oversized_system = "system evidence " * 12000
        messages = [{"role": "user", "content": "prior context " * 12000}]
        output = (
            'npm error code ENOENT\n'
            'npm error path /workspace/package.json\n'
            'npm error enoent Could not read package.json\n'
        )

        fitted = processor._fit_tool_output(
            oversized_system,
            messages,
            output,
            profile,
            wrapper_prefix="prefix",
            wrapper_suffix="suffix",
        )

        self.assertIn("ENOENT", fitted)
        self.assertIn("/workspace/package.json", fitted)

    def test_failed_host_tool_preserves_error_and_diagnostic_output(self):
        status, payload = _format_host_tool_result(
            success=False,
            output="npm error path /workspace/package.json",
            error="Command exited with code 254",
        )

        self.assertEqual(status, "error")
        self.assertIn("Command exited with code 254", payload)
        self.assertIn("npm error path /workspace/package.json", payload)

    def test_host_feedback_has_authoritative_status_before_untrusted_output(self):
        prefix = _host_tool_prefix("kitt_runtime", "success")

        self.assertTrue(prefix.startswith("kitt_runtime result from the host.\n"))
        self.assertIn("HOST_STATUS: success\n", prefix)
        self.assertLess(prefix.index("HOST_STATUS:"), prefix.index("UNTRUSTED_TOOL_OUTPUT:"))


if __name__ == "__main__":
    unittest.main()
