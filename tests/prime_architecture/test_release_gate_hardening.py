from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from kitt.core.pending_action import PendingAction
from kitt.core.runtime import KittRuntime
from kitt.extensions.manifest import parse_manifest_file
from kitt.extensions.plugins.loader import PluginLoader
from kitt.extensions.plugins.security import PluginStateStore, PluginTrustStore
from kitt.goals.scheduler import GoalScheduler
from kitt.runtime.safe_runtime import SafeRuntime
from kitt.security.capabilities import CAP_REPO_READ, CAP_REPO_WRITE
from kitt.security.context import ExecutionSecurityContext
from kitt.tools.handlers import ToolContext
from kitt.tools.handlers.safe_runtime import SafeRuntimeHandler
from kitt.tools.registry import ToolRegistry


class TestSafeRuntimeNewFileApproval(unittest.TestCase):
    def test_new_file_integrity_uses_none_not_string_sentinel(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
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
                        "new.py\n<<<<<<< SEARCH\n=======\nvalue = 1\n>>>>>>> REPLACE"
                    )
                },
            }
            result = SafeRuntimeHandler().execute(outer_args, context)
            self.assertTrue(result.requires_approval)

            pending = PendingAction(
                id="pa",
                approval_request_id="req",
                turn_id="turn",
                conversation_id="conv",
                workspace_id="ws",
                tool_name="kitt_runtime",
                normalized_args=outer_args,
                action_hash="hash",
                source_response_sha256="source",
                affected_paths=[],
                before_hashes={},
                created_at=time.time(),
                expires_at=time.time() + 60,
                state="pending",
                security_context=security.to_dict(),
            )
            self.assertEqual(pending.tool_name, "apply_patch")
            self.assertIsNone(pending.before_hashes["new.py"])
            self.assertIsNone(pending.security_context["approval_integrity"]["new.py"])


class TestPluginExternalTrust(unittest.IsolatedAsyncioTestCase):
    async def test_manifest_cannot_self_grant_trust_and_hash_change_revokes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plugin = root / ".kitt" / "plugins" / "demo"
            plugin.mkdir(parents=True)
            (plugin / "plugin.py").write_text(
                "def setup(ctx):\n    return None\n", encoding="utf-8"
            )
            (plugin / "plugin.toml").write_text(
                "\n".join(
                    [
                        'name = "demo"',
                        'version = "1.0.0"',
                        'api_version = "1"',
                        'entrypoint = "plugin:setup"',
                        "permissions = []",
                        "trusted_in_process = true",
                    ]
                ),
                encoding="utf-8",
            )
            manifest = parse_manifest_file(plugin / "plugin.toml")
            trust = PluginTrustStore(root, path=root / "trust.json")
            loader = PluginLoader(workspace_root=str(root), trust_store=trust)

            with self.assertRaises(Exception):
                await loader.load_async(manifest)

            trust.grant(manifest)
            instance = await loader.load_async(manifest)
            self.assertEqual(instance.state.value, "LOADED")

            (plugin / "plugin.py").write_text(
                "def setup(ctx):\n    return 'changed'\n", encoding="utf-8"
            )
            self.assertFalse(trust.is_trusted(manifest))
            with self.assertRaises(Exception):
                await loader.load_async(manifest)

    async def test_enable_disable_state_is_persistent_and_local(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state_path = root / "state.json"
            first = PluginStateStore(root, path=state_path)
            first.set_enabled("demo", False)
            enabled, disabled = PluginStateStore(root, path=state_path).load()
            self.assertNotIn("demo", enabled)
            self.assertIn("demo", disabled)
            PluginStateStore(root, path=state_path).set_enabled("demo", True)
            enabled, disabled = PluginStateStore(root, path=state_path).load()
            self.assertIn("demo", enabled)
            self.assertNotIn("demo", disabled)


class TestHandleFailClosed(unittest.TestCase):
    def test_direct_safe_runtime_handle_resolution_requires_principal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = SafeRuntime(temp_dir, "ws", "conv")
            result = runtime.execute(
                "handles.resolve",
                {"handle": "ctx:file:any.txt"},
                effective_capabilities={CAP_REPO_READ},
                security_context=None,
                origin="USER",
            )
            self.assertFalse(result.success)
            self.assertIn("ExecutionSecurityContext", result.error or "")


class TestGoalSideEffectFencing(unittest.TestCase):
    def test_stale_goal_principal_is_rejected_before_tool_execution(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "visible.txt").write_text("ok", encoding="utf-8")
            with KittRuntime.build(str(root)) as runtime:
                conversation = runtime.history.new_conversation("fence")
                goal = runtime.goals.create(
                    conversation["id"],
                    "read",
                    capabilities=[CAP_REPO_READ],
                    max_wall_seconds=60,
                )
                scheduler = GoalScheduler(runtime.database, runtime.goals)
                lease_id = scheduler._claim(goal.id)
                self.assertIsNotNone(lease_id)

                current = ExecutionSecurityContext(
                    workspace_id=runtime.workspace_id,
                    conversation_id=conversation["id"],
                    turn_id="turn",
                    origin="SCHEDULE",
                    principal_type="GOAL",
                    principal_id=goal.id,
                    capabilities=frozenset({CAP_REPO_READ}),
                    trace_id="trace",
                    fencing_token=lease_id,
                    fencing_owner_id=scheduler.worker_id,
                )
                ok = runtime.registry.execute_tool(
                    "read_file",
                    {"path": "visible.txt"},
                    turn_id="turn",
                    conversation_id=conversation["id"],
                    workspace_id=runtime.workspace_id,
                    origin="SCHEDULE",
                    security_context=current,
                )
                self.assertTrue(ok.success, ok.error)

                with runtime.database.get_connection() as conn:
                    conn.execute(
                        "UPDATE goals SET lease_id='stolen', lease_owner_id='other' WHERE id=?",
                        (goal.id,),
                    )
                denied = runtime.registry.execute_tool(
                    "read_file",
                    {"path": "visible.txt"},
                    turn_id="turn",
                    conversation_id=conversation["id"],
                    workspace_id=runtime.workspace_id,
                    origin="SCHEDULE",
                    security_context=current,
                )
                self.assertFalse(denied.success)
                self.assertIn("lease", (denied.error or "").lower())


if __name__ == "__main__":
    unittest.main()
