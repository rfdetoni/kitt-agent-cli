"""Regression for permanent host sandbox failures in the Agent tool loop."""

import unittest

from kitt.core.turn_tool_helpers import _nonretryable_host_environment_failure


class HostEnvironmentBlockerTests(unittest.TestCase):
    def test_missing_bubblewrap_blocks_unchanged_process_retry(self):
        error = (
            "ERROR: Command sandbox unavailable: Strong sandbox profile "
            "'workspace-write' requires functional bubblewrap >= 0.12"
        )
        self.assertTrue(_nonretryable_host_environment_failure(
            "kitt_runtime", {"operation": "process.run", "arguments": {"argv": ["pytest"]}}, error,
        ))
        self.assertTrue(_nonretryable_host_environment_failure(
            "run_command", {"argv": ["pytest"]}, error,
        ))

    def test_other_failures_can_be_replanned_or_retried(self):
        self.assertFalse(_nonretryable_host_environment_failure(
            "kitt_runtime", {"operation": "process.run"}, "pytest returned exit code 1",
        ))
        self.assertFalse(_nonretryable_host_environment_failure(
            "kitt_runtime", {"operation": "repo.read"}, "Command sandbox unavailable: bubblewrap",
        ))
        self.assertFalse(_nonretryable_host_environment_failure(
            "kitt_runtime", {"operation": "process.run"}, "Execution denied by PolicyEngine",
        ))


if __name__ == "__main__":
    unittest.main()
