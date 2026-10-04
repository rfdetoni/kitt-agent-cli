from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from kitt.core.agent_runtime import execute_tool_with_engineering
from kitt.tools.registry_core import ToolResult


class _Workspace:
    def __init__(self):
        self.refreshes = 0

    def refresh_owner(self, _owner_id, ttl_seconds=180.0):
        self.refreshes += 1
        return 1


class _RunCoordinator:
    def __init__(self):
        self.workspace = _Workspace()
        self.released = 0

    def claim_tool(self, *_args, **_kwargs):
        return []

    def release_tool(self, *_args, **_kwargs):
        self.released += 1
        return 1


class _ResourceCoordinator:
    def __init__(self):
        self.refreshes = 0
        self.released = 0

    def resources_for_tool(self, *_args, **_kwargs):
        return [object()]

    def acquire_many(self, *_args, **_kwargs):
        return []

    def refresh_owner(self, _owner_id, ttl_seconds=180.0):
        self.refreshes += 1
        return 1

    def release_owner(self, _owner_id):
        self.released += 1
        return 1


class AgentRuntimeToolLeaseTests(unittest.TestCase):
    def test_long_mutation_renews_parent_and_resource_leases(self):
        run_coordinator = _RunCoordinator()
        resource_coordinator = _ResourceCoordinator()
        processor = SimpleNamespace(
            run_coordinator=run_coordinator,
            resource_coordinator=resource_coordinator,
            execution_budgets={},
            workspace_snapshot_service=None,
            task_plans=None,
            cancellation_registry=None,
            turn_journal=None,
        )
        registry = SimpleNamespace(
            process_runner=SimpleNamespace(
                sandbox=SimpleNamespace(default_profile="workspace-write")
            ),
            policy=SimpleNamespace(autonomy=object()),
            approval_manager=object(),
        )

        def execute(_name, _args, *_pos, **_kwargs):
            time.sleep(0.05)
            return ToolResult(True, "ok")

        with (
            patch("kitt.core.agent_runtime.TOOL_LEASE_HEARTBEAT_SECONDS", 0.01),
            patch(
                "kitt.core.agent_runtime.capture_authority_snapshot",
                return_value=object(),
            ),
            patch("kitt.core.agent_runtime.validate_authority_snapshot"),
        ):
            result = execute_tool_with_engineering(
                processor,
                registry,
                execute,
                "run_command",
                {"argv": ["python", "-V"]},
                conversation_id="conv-1",
                turn_id="turn-1",
                security_context=object(),
            )

        self.assertTrue(result.success)
        self.assertGreater(run_coordinator.workspace.refreshes, 0)
        self.assertGreater(resource_coordinator.refreshes, 0)
        self.assertEqual(run_coordinator.released, 1)
        self.assertEqual(resource_coordinator.released, 1)


if __name__ == "__main__":
    unittest.main()
