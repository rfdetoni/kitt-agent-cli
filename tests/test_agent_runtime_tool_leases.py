from __future__ import annotations

import time
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from kitt.core.agent_runtime import execute_tool_with_engineering
from kitt.core.run_coordinator import RunCoordinator
from kitt.core.workspace_snapshot import WorkspaceSnapshotService
from kitt.security.workspace_fs import WorkspaceFileSystem
from kitt.tools.handlers import ToolContext
from kitt.tools.handlers.files import CreateDirectoryHandler
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
    def test_directory_creation_on_existing_git_checkout_skips_snapshot(self):
        with tempfile.TemporaryDirectory() as root_dir:
            root = Path(root_dir)
            git_dir = root / "mbs-wine" / ".git"
            git_dir.mkdir(parents=True)
            (git_dir / "HEAD").write_text("ref: refs/heads/main\\n")
            fs = WorkspaceFileSystem(root)
            snapshots = WorkspaceSnapshotService(
                root, workspace_id="test-workspace", artifact_store=object()
            )
            processor = SimpleNamespace(
                run_coordinator=SimpleNamespace(
                    mutation_paths=RunCoordinator.mutation_paths
                ),
                workspace_snapshot_service=snapshots,
                execution_budgets={},
                task_plans=None,
                turn_journal=None,
            )
            verifier = SimpleNamespace(
                verify=lambda *args, **kwargs: SimpleNamespace(
                    ok=True, as_dict=lambda: {"ok": True}
                )
            )
            registry = SimpleNamespace(root_path=root, _agent_verifier=verifier)

            def create_dir(_name, args, *_pos, **_kwargs):
                return CreateDirectoryHandler().execute(
                    args["arguments"],
                    ToolContext(
                        registry=registry,
                        turn_id="turn",
                        conversation_id="conversation",
                        workspace_id="test-workspace",
                        origin="MODEL",
                    ),
                )

            with (
                patch.object(snapshots, "capture", wraps=snapshots.capture) as capture,
                patch.object(verifier, "verify", wraps=verifier.verify) as verify,
            ):
                result = execute_tool_with_engineering(
                    processor, registry, create_dir,
                    "kitt_runtime",
                    {"operation": "repo.create_directory", "arguments": {"path": "mbs-wine"}},
                    conversation_id="conversation", turn_id="turn",
                )
                self.assertTrue(result.success)
                self.assertFalse(result.metadata["created"])
                self.assertEqual(result.metadata["changed_paths"], [])
                self.assertIn("no changes made", result.output)
                capture.assert_not_called()
                verify.assert_not_called()
                self.assertEqual((git_dir / "HEAD").read_text(), "ref: refs/heads/main\\n")

                created = execute_tool_with_engineering(
                    processor, registry, create_dir,
                    "kitt_runtime",
                    {"operation": "repo.create_directory", "arguments": {"path": "new-directory"}},
                    conversation_id="conversation", turn_id="turn",
                )
                self.assertTrue(created.success)
                self.assertTrue(created.metadata["created"])
                self.assertEqual(created.metadata["changed_paths"], ["new-directory"])
                capture.assert_called_once()
                verify.assert_called_once()
                self.assertEqual(
                    created.metadata.get("workspace_snapshot_id"),
                    snapshots._last_by_turn.get("turn"),
                )
                self.assertTrue((root / "new-directory").is_dir())

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
