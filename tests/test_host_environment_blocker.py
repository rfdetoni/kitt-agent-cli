"""Regression for permanent host sandbox failures in the Agent tool loop."""

import unittest

from kitt.core.turn_tool_helpers import (
    _nonretryable_host_environment_failure,
    _stuck_sequence,
    _tool_stall_fingerprint,
)


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

    def test_alternating_failed_command_and_unchanged_read_is_stalled(self):
        failed = _tool_stall_fingerprint(
            "kitt_runtime", {"operation": "process.run", "arguments": {"argv": ["pytest"]}},
            success=False, output="1 failed", error="exit code 1",
        )
        observation = _tool_stall_fingerprint(
            "kitt_runtime", {"operation": "repo.read", "arguments": {"path": "README.md"}},
            success=True, output="unchanged file", error=None,
        )
        self.assertTrue(_stuck_sequence([failed, observation, failed, observation]))
        # A successful mutation is evidence of progress, so the loop resets.
        self.assertIsNone(_tool_stall_fingerprint(
            "kitt_runtime", {"operation": "repo.write_file", "arguments": {"path": "a"}},
            success=True, output="written", error=None,
        ))

    def test_repeated_successful_read_batches_do_not_reset_stall_detection(self):
        arguments = {"operation": "repo.batch_read", "arguments": {
            "calls": [{"id": "one", "operation": "repo.read",
                       "arguments": {"path": "README.md"}}],
        }}
        observed = _tool_stall_fingerprint(
            "kitt_runtime", arguments, success=True,
            output='{"calls":[{"id":"one","success":true}]}', error=None,
        )
        self.assertIsNotNone(observed)
        self.assertTrue(_stuck_sequence([observed, observed, observed]))
        self.assertIsNone(_tool_stall_fingerprint(
            "kitt_runtime", {"operation": "repo.write_file", "arguments": {"path": "x"}},
            success=True, output="written", error=None,
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
