from __future__ import annotations

import unittest
from unittest.mock import patch

from kitt.core.runtime_config import RuntimeConfig
from kitt.core.turn_tool_helpers import _completion_recovery_exhausted


class RecoveryLimitsTests(unittest.TestCase):
    def test_limits_are_bounded_and_configurable_without_weakening_host_policy(self):
        variables = {
            "KITT_MAX_MALFORMED_TOOL_RECOVERIES": "1",
            "KITT_MAX_POLICY_DENIAL_RECOVERIES": "0",
            "KITT_MAX_OUTPUT_LIMIT_RECOVERIES": "3",
            "KITT_MAX_COMPLETION_RECOVERIES": "1",
            "KITT_MAX_PYTHON_CALLS_PER_TURN": "1",
        }
        with patch.dict("os.environ", variables), patch(
            "kitt.core.runtime_config.runtime_overrides", return_value={}
        ):
            config = RuntimeConfig.from_env()
        self.assertEqual(config.max_policy_denial_recoveries, 0)
        self.assertEqual(config.max_malformed_tool_recoveries, 1)
        self.assertEqual(config.max_output_limit_recoveries, 3)
        self.assertEqual(config.max_python_calls_per_turn, 1)
        self.assertFalse(_completion_recovery_exhausted(1, config.max_completion_recoveries))
        self.assertTrue(_completion_recovery_exhausted(2, config.max_completion_recoveries))

    def test_invalid_limits_fail_closed(self):
        for value in ("-1", "9", "not-an-integer"):
            with self.subTest(value=value), patch.dict(
                "os.environ", {"KITT_MAX_POLICY_DENIAL_RECOVERIES": value}
            ), patch("kitt.core.runtime_config.runtime_overrides", return_value={}):
                with self.assertRaises(ValueError):
                    RuntimeConfig.from_env()
