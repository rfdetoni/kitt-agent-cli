from __future__ import annotations

import hashlib
import tempfile
import time
import unittest
from pathlib import Path

from kitt.children.context import narrow_child_paths
from kitt.core.pending_action import PendingAction, canonical_args_digest
from kitt.core.runtime import KittRuntime
from kitt.domain.entities import SemanticConfidence
from kitt.goals.scheduler import GoalScheduler
from kitt.llm.client import LLMClient
from kitt.runtime.safe_runtime import SafeRuntime
from kitt.security.capabilities import CAP_REPO_READ, CAP_REPO_SEARCH, CAP_REPO_WRITE
from kitt.security.context import ExecutionSecurityContext
from kitt.tools.handlers import ToolContext
from kitt.tools.handlers.safe_runtime import SafeRuntimeHandler
from kitt.tools.handlers.system import ApplyPatchHandler, RunCommandHandler
from kitt.tools.registry import ToolRegistry
from kitt.tools.surface_selector import ToolSurfaceSelector
from kitt.ui.model_commands import parse_model_command


class TestPythonCompatibility(unittest.TestCase):
    def test_forward_annotations_import_on_supported_python(self):
        self.assertEqual(SemanticConfidence.from_overall(0.5).overall, 0.5)
        self.assertEqual(
            parse_model_command("principal ollama qwen2.5-coder")[:3],
            ("principal", "qwen2.5-coder", "ollama"),
        )


    def test_auto_surface_uses_existing_model_capabilities_contract(self):
        from kitt.domain.entities import ContextPlan, ModelProfile

        plan = ContextPlan(enabled_tools=["read_file", "search", "apply_patch"])
        local_client = LLMClient(
            ModelProfile(
                backend="ollama", model="local", context_window=8192,
                max_output_tokens=1024
            )
        )
        cloud_client = LLMClient(
            ModelProfile(
                backend="openai", model="large", context_window=128000,
                max_output_tokens=4096
            )
        )
        try:
            selector = ToolSurfaceSelector()
            self.assertEqual(
                selector.select_tools(plan, local_client.capabilities),
                ["kitt_runtime"],
            )
            self.assertEqual(
                selector.select_tools(plan, cloud_client.capabilities),
                plan.enabled_tools,
            )
        finally:
            local_client.close()
            cloud_client.close()


class TestCompositeApprovalIntegrity(unittest.TestCase):
    def test_safe_runtime_patch_approval_becomes_exact_concrete_pending_action(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "a.txt"
            target.write_text("old\n", encoding="utf-8")
            registry = ToolRegistry(str(root))
            security = ExecutionSecurityContext.create_user_context(
                "ws", "conv", "turn", capabilities={CAP_REPO_WRITE}
            )
            context = ToolContext(
                registry=registry,
                turn_id="turn",
                conversation_id="conv",
                workspace_id="ws",
                origin="MODEL",
                security_context=security,
            )
            outer_args = {
                "operation": "patch.apply",
                "arguments": {
                    "patch": (
                        "a.txt\n<<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>> REPLACE"
                    )
                },
            }
            result = SafeRuntimeHandler().execute(outer_args, context)
            self.assertTrue(result.requires_approval)
            self.assertEqual(result.metadata["resume_tool_name"], "apply_patch")

            pending = PendingAction(
                id="pa_turn",
                approval_request_id="req",
                turn_id="turn",
                conversation_id="conv",
                workspace_id="ws",
                tool_name="kitt_runtime",
                normalized_args=outer_args,
                action_hash="action",
                source_response_sha256="outer",
                affected_paths=[],
                before_hashes={},
                created_at=time.time(),
                expires_at=time.time() + 60,
                state="pending",
                security_context=security.to_dict(),
            )
            concrete = result.metadata["approval_payload"]
            self.assertEqual(pending.tool_name, "apply_patch")
            self.assertEqual(pending.normalized_args, concrete)
            self.assertEqual(
                pending.source_response_sha256, canonical_args_digest(concrete)
            )
            self.assertEqual(pending.affected_paths, ["a.txt"])
            self.assertEqual(
                pending.before_hashes["a.txt"],
                hashlib.sha256(target.read_bytes()).hexdigest(),
            )


class TestPathScope(unittest.TestCase):
    def test_parent_scope_can_only_be_narrowed(self):
        parent = ExecutionSecurityContext.create_user_context(
            "ws",
            "conv",
            capabilities={CAP_REPO_READ, CAP_REPO_WRITE},
            path_scope=["src/auth"],
        )
        child = parent.derive_child_context(
            "child",
            [CAP_REPO_READ, CAP_REPO_WRITE],
            allowed_paths=["src"],
        )
        self.assertEqual(child.path_scope, frozenset({"src/auth"}))
        self.assertTrue(child.allows_path("src/auth/token.py"))
        self.assertFalse(child.allows_path("src/payment/card.py"))
        with self.assertRaises(PermissionError):
            parent.derive_child_context(
                "child2", [CAP_REPO_READ], allowed_paths=["docs"]
            )

    def test_files_and_search_respect_scope(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "allowed").mkdir()
            (root / "blocked").mkdir()
            (root / "allowed" / "visible.txt").write_text("needle visible")
            (root / "blocked" / "secret.txt").write_text("needle secret")
            registry = ToolRegistry(str(root))
            security = ExecutionSecurityContext.create_user_context(
                "ws",
                "conv",
                capabilities={CAP_REPO_READ, CAP_REPO_SEARCH},
                path_scope=["allowed"],
            )

            visible = registry.execute_tool(
                "read_file",
                {"path": "allowed/visible.txt"},
                conversation_id="conv",
                workspace_id="ws",
                origin="USER",
                security_context=security,
            )
            self.assertTrue(visible.success, visible.error)

            blocked = registry.execute_tool(
                "read_file",
                {"path": "blocked/secret.txt"},
                conversation_id="conv",
                workspace_id="ws",
                origin="USER",
                security_context=security,
            )
            self.assertFalse(blocked.success)
            self.assertIn("path scope", blocked.error)

            search = registry.execute_tool(
                "search",
                {"pattern": "needle", "regex": True},
                conversation_id="conv",
                workspace_id="ws",
                origin="USER",
                security_context=security,
            )
            self.assertTrue(search.success, search.error)
            self.assertIn("allowed/visible.txt", search.output)
            self.assertNotIn("blocked/secret.txt", search.output)

    def test_safe_runtime_preserves_scope_when_delegating(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "allowed").mkdir()
            (root / "blocked").mkdir()
            (root / "allowed" / "visible.txt").write_text("visible")
            (root / "blocked" / "secret.txt").write_text("secret")
            registry = ToolRegistry(str(root))
            runtime = SafeRuntime(root, "ws", "conv", tool_registry=registry)
            security = ExecutionSecurityContext.create_user_context(
                "ws",
                "conv",
                capabilities={CAP_REPO_READ},
                path_scope=["allowed"],
            )
            ok = runtime.execute(
                "repo.read",
                {"path": "allowed/visible.txt"},
                origin="USER",
                security_context=security,
            )
            denied = runtime.execute(
                "repo.read",
                {"path": "blocked/secret.txt"},
                origin="USER",
                security_context=security,
            )
            self.assertTrue(ok.success, ok.error)
            self.assertFalse(denied.success)
            self.assertIn("path scope", denied.error)

    def test_mutating_handlers_fail_closed_for_scoped_principal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "allowed").mkdir()
            (root / "blocked").mkdir()
            (root / "blocked" / "a.txt").write_text("old")
            registry = ToolRegistry(str(root))
            security = ExecutionSecurityContext.create_user_context(
                "ws",
                "conv",
                capabilities={CAP_REPO_WRITE},
                path_scope=["allowed"],
            )
            context = ToolContext(
                registry=registry,
                turn_id="turn",
                conversation_id="conv",
                workspace_id="ws",
                origin="USER",
                security_context=security,
            )
            patch = ApplyPatchHandler().execute(
                {
                    "patch": (
                        "blocked/a.txt\n<<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>> REPLACE"
                    )
                },
                context,
            )
            self.assertFalse(patch.success)
            command = RunCommandHandler().execute(
                {"command": "python -c 'print(1)'"}, context
            )
            self.assertFalse(command.success)
            self.assertIn("path-scoped", command.error)

    def test_child_path_intersection_helper(self):
        self.assertEqual(
            narrow_child_paths("/tmp/example", ["src"], ["src/auth"]),
            ["src/auth"],
        )


class TestCompositionAndScheduler(unittest.TestCase):
    def test_runtime_wires_child_manager_and_registry_observer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with KittRuntime.build(temp_dir) as runtime:
                self.assertIs(runtime.processor.child_manager, runtime.children)
                self.assertIs(runtime.registry._processor, runtime.processor)


    def test_approved_child_action_completion_hook_is_wired(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with KittRuntime.build(temp_dir) as runtime:
                conversation = runtime.history.new_conversation("child approval")
                child = runtime.children.spawn(
                    parent_conversation_id=conversation["id"],
                    parent_turn_id="parent_turn",
                    name="approval_child",
                    task="initial",
                    worker=lambda _task: "ready",
                    allowed_tools=["read_file"],
                )
                runtime.children.wait(child.id, timeout=5.0)
                runtime.children.repo.update(
                    child.id, state="WAITING_APPROVAL", current_task_id="child_turn"
                )
                self.assertTrue(
                    runtime.children.on_approved_action_executed(
                        child.id, "child_turn", "approved tool output"
                    )
                )
                completed = runtime.children.inspect(child.id)
                self.assertEqual(completed.state, "COMPLETED")
                self.assertIsNotNone(completed.result_artifact_id)

    def test_scheduler_renews_lease_with_owner_cas(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with KittRuntime.build(temp_dir) as runtime:
                conversation = runtime.history.new_conversation("scheduler")
                goal = runtime.goals.create(
                    conversation["id"], "test", max_wall_seconds=60
                )
                scheduler = GoalScheduler(
                    runtime.database,
                    runtime.goals,
                    lease_duration_seconds=1.0,
                )
                lease_id = scheduler._claim(goal.id)
                self.assertIsNotNone(lease_id)
                before = runtime.goals.get(goal.id).lease_expires_at
                time.sleep(0.05)
                self.assertTrue(scheduler._heartbeat_lease_once(goal.id, lease_id))
                after = runtime.goals.get(goal.id).lease_expires_at
                self.assertGreater(after, before)
                self.assertTrue(
                    scheduler._release(
                        goal.id, lease_id, state="ACTIVE", next_run=None
                    )
                )


if __name__ == "__main__":
    unittest.main()
