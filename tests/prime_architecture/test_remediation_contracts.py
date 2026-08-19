from __future__ import annotations

import dataclasses
import tempfile
import unittest
from pathlib import Path

from kitt.core.runtime_config import RuntimeConfig
from kitt.security.context import ExecutionSecurityContext
from kitt.security.capabilities import CAP_REPO_READ, CAP_REPO_WRITE
from kitt.ui.daemon_bridge import map_daemon_event_to_turn_event
from kitt.daemon.protocol import DaemonEvent
from kitt.core.turn_events import ToolCompleted


class TestRemediationContracts(unittest.TestCase):
    def test_security_context_defaults_fail_closed(self):
        ctx = ExecutionSecurityContext.create_user_context("ws", "conv")
        self.assertEqual(ctx.capabilities, frozenset())

    def test_child_cannot_escalate(self):
        parent = ExecutionSecurityContext.create_user_context(
            "ws", "conv", capabilities={CAP_REPO_READ}
        )
        child = parent.derive_child_context(
            "child", [CAP_REPO_READ, CAP_REPO_WRITE],
            workspace_policy_caps=[CAP_REPO_READ, CAP_REPO_WRITE],
        )
        self.assertEqual(child.capabilities, frozenset({CAP_REPO_READ}))

    def test_daemon_mapper_ignores_schema_drift(self):
        event = DaemonEvent(
            sequence_id=1, session_id="s", event_type="ToolCompleted",
            payload={
                "tool_name": "read_file", "success": True, "output": "ok",
                "duration_ms": 123, "tokens_saved": 9,  # fields not in current dataclass
            },
            created_at=0,
        )
        mapped = map_daemon_event_to_turn_event(event)
        self.assertIsInstance(mapped, ToolCompleted)
        self.assertEqual(mapped.tool_name, "read_file")

    def test_runtime_config_env_mode_validation(self):
        self.assertIn(RuntimeConfig().tool_runtime_mode, {"legacy", "safe_runtime", "auto"})


if __name__ == "__main__":
    unittest.main()
