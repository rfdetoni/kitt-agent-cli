import unittest

from kitt.learn.service import LearnService, safe_tool_signature


class LearnPrivacyTests(unittest.TestCase):
    def test_tool_portfolio_signature_does_not_expose_paths_or_secret_arguments(self):
        payload = {
            "tool_name": "kitt_runtime",
            "args": {
                "operation": "process.run",
                "arguments": {
                    "argv": [
                        "curl",
                        "https://example.invalid/private",
                        "--header",
                        "Authorization: Bearer super-secret-token",
                    ]
                },
            },
        }

        signature = safe_tool_signature(payload)

        self.assertEqual(signature, "Bash(other)")
        self.assertNotIn("secret", signature)
        self.assertNotIn("example.invalid", signature)

    def test_read_signature_keeps_only_file_classification(self):
        signature = safe_tool_signature(
            {
                "tool_name": "kitt_runtime",
                "args": {
                    "operation": "repo.read",
                    "arguments": {
                        "path": "customers/acme/private/VerySensitive.java"
                    },
                },
            }
        )

        self.assertEqual(signature, "Read(*.java)")
        self.assertNotIn("acme", signature)
        self.assertNotIn("VerySensitive", signature)

    def test_experiment_never_auto_promotes_candidate(self):
        control = {
            "observed": True,
            "turns": 2,
            "success": {"rate": 1.0},
            "validation": {"observed": True, "rate": 1.0},
            "tokens": {"input": 1000, "output": 500},
            "latency": {"avg_ms": 1000.0},
        }
        candidate = {
            "observed": True,
            "turns": 2,
            "success": {"rate": 1.0},
            "validation": {"observed": True, "rate": 1.0},
            "tokens": {"input": 700, "output": 300},
            "latency": {"avg_ms": 700.0},
        }

        verdict = LearnService._experiment_verdict(control, candidate)

        self.assertEqual(verdict["state"], "CANDIDATE_BETTER")
        self.assertTrue(verdict["measurable_gain"])
        self.assertFalse(verdict["promote"])


if __name__ == "__main__":
    unittest.main()
