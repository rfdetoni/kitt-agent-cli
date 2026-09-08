import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from kitt.security.path_policy import PathPolicy
from kitt.tools.registry import ToolRegistry, ToolResult


class _Store:
    last_value = None

    def __init__(self, *_args, **_kwargs):
        pass

    def set(self, key, value, ttl_seconds=None):
        type(self).last_value = (key, value, ttl_seconds)
        return True


class TestGoalReviewApprovalContinuation(unittest.TestCase):
    def test_approved_goal_action_persists_only_contained_affected_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            registry = object.__new__(ToolRegistry)
            registry.db = object()
            registry.root_path = root
            registry.path_policy = PathPolicy(root)
            registry.goal_service = SimpleNamespace(
                resume_after_approval=lambda *_a, **_k: None
            )
            registry.event_bus = None
            registry.child_manager = None

            result = ToolResult(
                True,
                "approved write completed",
                metadata={
                    "path": "service.py",
                    "changed_paths": ["tests/test_service.py", "../escape.py"],
                    "edit_result": SimpleNamespace(
                        applied_files=["service.py"],
                        created_files=["new_module.py"],
                    ),
                },
            )
            security = SimpleNamespace(
                principal_type="GOAL",
                principal_id="goal_1",
                workspace_id="ws",
                conversation_id="conv",
            )

            _Store.last_value = None
            with patch("kitt.runtime.state.RuntimeStateStore", _Store):
                registry._record_approved_principal_continuation(
                    security, "turn_1", result
                )

            key, value, ttl = _Store.last_value
            self.assertEqual(key, "goal.resume:goal_1")
            self.assertEqual(ttl, 3600.0)
            self.assertEqual(
                value["affected_paths"],
                ["service.py", "new_module.py", "tests/test_service.py"],
            )
            self.assertNotIn("../escape.py", value["affected_paths"])


if __name__ == "__main__":
    unittest.main()
