"""Decision-model options belong to KITT's reverse-proxy configuration panel."""
from __future__ import annotations

import unittest

from kitt.ui.reverse_proxy_panel import _parse_decision_flags
from kitt.reverse_proxy.contracts import ReverseProxyInstance


class DecisionModelUiTests(unittest.TestCase):
    def test_parse_decision_flags_for_single_managed_service(self):
        args = ["configure", "coding", "--decision-mode", "assist",
                "--decision-model", "winnow:e4b",
                "--temporary-chat", "supported",
                "--decision-timeout-ms", "1800"]
        self.assertEqual(_parse_decision_flags(args), {
            "decision_mode": "assist",
            "decision_model": "winnow:e4b",
            "temporary_chat": "supported",
            "decision_timeout_ms": 1800,
        })

    def test_missing_option_value_fails(self):
        with self.assertRaisesRegex(ValueError, "Missing value"):
            _parse_decision_flags(["configure", "coding", "--decision-model"])

    def test_instance_contract_displays_saved_model_settings(self):
        record = ReverseProxyInstance.from_dict({
            "id": "coding", "provider": "chatgpt", "model": "chatgpt-web",
            "target": "chatgpt", "profileId": "coding",
            "host": "127.0.0.1", "port": 3000,
            "pid": 11, "startedAt": "now",
            "decisionMode": "shadow",
            "decisionModel": "laya",
            "temporaryChat": "required",
        })
        self.assertEqual(record.decision_mode, "shadow")
        self.assertEqual(record.temporary_chat, "required")
        self.assertEqual(record.decision_model, "laya")


if __name__ == "__main__":
    unittest.main()
