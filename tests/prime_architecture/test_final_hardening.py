from __future__ import annotations

import asyncio
import tempfile
import threading
import time
import unittest
from pathlib import Path

from kitt.artifacts.store import ArtifactStore
from kitt.children.manager import ChildAgentManager
from kitt.children.repository import ChildRepository
from kitt.core.autonomy_store import AutonomyStore
from kitt.extensions.errors import PluginLoadError
from kitt.extensions.manifest import parse_manifest_data, parse_manifest_file
from kitt.extensions.plugins.loader import PluginLoader
from kitt.extensions.plugins.security import PluginTrustStore
from kitt.goals.scheduler import GoalScheduler, LeaseLostError
from kitt.goals.service import GoalService
from kitt.history.database import HistoryDatabase
from kitt.runtime.handles import ContextHandleResolver
from kitt.security.capabilities import (
    CAP_ARTIFACT_READ,
    CAP_CHILD_INSPECT,
    CAP_GOAL_MANAGE,
    CAP_REPO_READ,
    CAP_REPO_WRITE,
)
from kitt.security.context import ExecutionSecurityContext
from kitt.skills.subprocess_sandbox import SubprocessSkillSandbox
from kitt.tools.registry import ToolRegistry, ToolResult


class TestHandleCapabilityIsolation(unittest.TestCase):
    def test_artifact_handle_requires_artifact_capability(self):
        class Artifact:
            workspace_id = "ws"
            conversation_id = "conv"
            artifact_type = "TEXT"
            summary = "secret"

        class Store:
            def get(self, _artifact_id):
                return Artifact()

            def read_text_page(self, *_args, **_kwargs):
                return {
                    "content": "classified",
                    "has_more": False,
                    "total_bytes": 10,
                }

        resolver = ContextHandleResolver(
            ".",
            artifact_store=Store(),
            workspace_id="ws",
            conversation_id="conv",
        )
        repo_only = ExecutionSecurityContext.create_user_context(
            "ws",
            "conv",
            capabilities={CAP_REPO_READ},
        )
        with self.assertRaises(PermissionError):
            resolver.resolve("artifact:a1", security_context=repo_only)

        allowed = ExecutionSecurityContext.create_user_context(
            "ws",
            "conv",
            capabilities={CAP_ARTIFACT_READ},
        )
        result = resolver.resolve("artifact:a1", security_context=allowed)
        self.assertEqual(result["content"], "classified")

    def test_child_and_goal_handles_require_domain_capabilities(self):
        class Child:
            name = "worker"
            state = "IDLE"
            result_artifact_id = None
            error = None

        class Children:
            def inspect(self, *_args, **_kwargs):
                return Child()

        class Goal:
            objective = "objective"
            state = "ACTIVE"
            turns_used = 0
            tokens_used = 0

        class Goals:
            def get_scoped(self, *_args, **_kwargs):
                return Goal()

        resolver = ContextHandleResolver(
            ".",
            child_manager=Children(),
            goal_service=Goals(),
            workspace_id="ws",
            conversation_id="conv",
        )
        repo_only = ExecutionSecurityContext.create_user_context(
            "ws", "conv", capabilities={CAP_REPO_READ}
        )
        with self.assertRaises(PermissionError):
            resolver.resolve("child:c1", security_context=repo_only)
        with self.assertRaises(PermissionError):
            resolver.resolve("goal:g1", security_context=repo_only)

        child_ctx = ExecutionSecurityContext.create_user_context(
            "ws", "conv", capabilities={CAP_CHILD_INSPECT}
        )
        self.assertEqual(
            resolver.resolve("child:c1", security_context=child_ctx)["name"],
            "worker",
        )
        goal_ctx = ExecutionSecurityContext.create_user_context(
            "ws", "conv", capabilities={CAP_GOAL_MANAGE}
        )
        self.assertEqual(
            resolver.resolve("goal:g1", security_context=goal_ctx)["objective"],
            "objective",
        )


class TestSkillScopeInheritance(unittest.TestCase):
    def test_skill_context_cannot_escape_parent_scope_or_capabilities(self):
        parent = ExecutionSecurityContext.create_user_context(
            "ws",
            "conv",
            capabilities={CAP_REPO_READ},
            path_scope=["src/auth"],
        )
        skill = parent.derive_skill_context(
            "skill:test",
            requested_capabilities={CAP_REPO_READ, CAP_REPO_WRITE},
        )
        self.assertEqual(skill.capabilities, frozenset({CAP_REPO_READ}))
        self.assertEqual(skill.path_scope, frozenset({"src/auth"}))
        self.assertTrue(skill.allows_path("src/auth/token.py"))
        self.assertFalse(skill.allows_path("src/billing/card.py"))

    def test_sandbox_rpc_uses_derived_skill_context(self):
        captured = {}

        class SafeRuntime:
            def execute(self, method, params, origin, security_context):
                captured["method"] = method
                captured["context"] = security_context
                return type(
                    "R",
                    (),
                    {
                        "success": True,
                        "data": "ok",
                        "error": None,
                        "requires_approval": False,
                        "approval_action": None,
                    },
                )()

        parent = ExecutionSecurityContext.create_user_context(
            "ws",
            "conv",
            capabilities={CAP_REPO_READ},
            path_scope=["allowed"],
        )
        response = SubprocessSkillSandbox(SafeRuntime())._handle_rpc(
            "repo.read",
            {"path": "allowed/a.py"},
            [CAP_REPO_READ, CAP_REPO_WRITE],
            parent,
        )
        self.assertTrue(response["success"])
        context = captured["context"]
        self.assertEqual(context.capabilities, frozenset({CAP_REPO_READ}))
        self.assertEqual(context.path_scope, frozenset({"allowed"}))


class TestAutonomyUpdates(unittest.TestCase):
    def test_partial_override_is_not_discarded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = AutonomyStore(temp_dir, persistence_enabled=False)
            policy = store.update(
                allow_file_write_auto=True,
                max_auto_actions_per_turn=7,
            )
            self.assertTrue(policy.allow_file_write_auto)
            self.assertEqual(policy.max_auto_actions_per_turn, 7)
            self.assertEqual(policy.level, "supervised")

    def test_unknown_override_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = AutonomyStore(temp_dir, persistence_enabled=False)
            with self.assertRaises(ValueError):
                store.update(nonexistent_flag=True)


class TestPluginTrustBoundary(unittest.IsolatedAsyncioTestCase):
    async def test_untrusted_plugin_is_not_imported(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plugin = root / ".kitt" / "plugins" / "evil"
            plugin.mkdir(parents=True)
            marker = root / "executed.txt"
            (plugin / "plugin.py").write_text(
                f"from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text('executed')\n"
                "def setup(ctx):\n"
                "    return None\n",
                encoding="utf-8",
            )
            (plugin / "plugin.toml").write_text(
                "\n".join(
                    [
                        'name = "evil"',
                        'version = "1.0.0"',
                        'api_version = "1"',
                        'entrypoint = "plugin:setup"',
                        "permissions = []",
                    ]
                ),
                encoding="utf-8",
            )
            manifest = parse_manifest_file(
                plugin / "plugin.toml",
                source="workspace",
            )
            loader = PluginLoader(workspace_root=str(root))
            with self.assertRaises(PluginLoadError):
                await loader.load_async(manifest)
            self.assertFalse(marker.exists())

    async def test_trusted_async_setup_is_awaited(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plugin = root / ".kitt" / "plugins" / "async_ok"
            plugin.mkdir(parents=True)
            marker = root / "setup.txt"
            (plugin / "plugin.py").write_text(
                "import asyncio\n"
                "async def setup(ctx):\n"
                "    await asyncio.sleep(0)\n"
                f"    open({str(marker)!r}, 'w').write('ready')\n"
                "    return None\n",
                encoding="utf-8",
            )
            (plugin / "plugin.toml").write_text(
                "\n".join(
                    [
                        'name = "async_ok"',
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
            loader = PluginLoader(
                workspace_root=str(root),
                trust_store=PluginTrustStore(root, path=root / "trust.json"),
            )
            loader.trust_store.grant(manifest)
            instance = await loader.load_async(manifest)
            self.assertTrue(marker.exists())
            self.assertEqual(instance.state.value, "LOADED")

    def test_trusted_in_process_must_be_boolean(self):
        with self.assertRaises(Exception):
            parse_manifest_data(
                {
                    "name": "bad",
                    "version": "1",
                    "api_version": "1",
                    "entrypoint": "plugin:setup",
                    "permissions": [],
                    "trusted_in_process": "yes",
                }
            )


class TestSchedulerFencing(unittest.TestCase):
    def _seed(self, root: Path):
        db = HistoryDatabase(str(root))
        goals = GoalService(db)
        with db.get_connection() as connection:
            now = time.time()
            connection.execute(
                """INSERT OR IGNORE INTO workspaces
                   (id, canonical_path_hash, display_name, created_at, last_opened_at)
                   VALUES (?, ?, ?, ?, ?)""",
                ("ws", "hash", "ws", now, now),
            )
            connection.execute(
                """INSERT OR IGNORE INTO conversations
                   (id, workspace_id, title, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                ("conv", "ws", "conv", now, now),
            )
        goal = goals.create("conv", "objective", max_wall_seconds=60)
        return db, goals, goal

    def test_stale_worker_result_is_fenced(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db, goals, goal = self._seed(Path(temp_dir))
            try:
                scheduler = GoalScheduler(
                    db,
                    goals,
                    lease_duration_seconds=5.0,
                )
                lease_id = scheduler._claim(goal.id)
                self.assertIsNotNone(lease_id)

                def steal(_goal):
                    with db.get_connection() as connection:
                        connection.execute(
                            """UPDATE goals
                               SET lease_owner_id='other', lease_id='other-lease',
                                   lease_expires_at=?
                               WHERE id=?""",
                            (time.time() + 30, goal.id),
                        )
                    return {"status": "SUCCEEDED", "tokens": 999}

                scheduler.set_executor(steal)
                with self.assertRaises(LeaseLostError):
                    scheduler._execute_with_heartbeat(goal, lease_id)
                self.assertEqual(goals.get(goal.id).tokens_used, 0)
            finally:
                db.close()


class TestGoalApprovalCheckpoint(unittest.TestCase):
    def test_registry_records_goal_approved_output_in_runtime_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db = HistoryDatabase(str(root))
            try:
                with db.get_connection() as connection:
                    now = time.time()
                    connection.execute(
                        """INSERT OR IGNORE INTO workspaces
                           (id, canonical_path_hash, display_name, created_at, last_opened_at)
                           VALUES (?, ?, ?, ?, ?)""",
                        ("ws", "hash-goal-checkpoint", "ws", now, now),
                    )
                    connection.execute(
                        """INSERT OR IGNORE INTO conversations
                           (id, workspace_id, title, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?)""",
                        ("conv", "ws", "conv", now, now),
                    )
                registry = ToolRegistry(str(root))
                registry.db = db
                context = ExecutionSecurityContext(
                    workspace_id="ws",
                    conversation_id="conv",
                    turn_id="turn",
                    origin="SCHEDULE",
                    principal_type="GOAL",
                    principal_id="goal1",
                    capabilities=frozenset({CAP_REPO_READ}),
                    trace_id="trace",
                )
                registry._record_approved_principal_continuation(
                    context,
                    "turn",
                    ToolResult(True, "approved output"),
                )
                from kitt.runtime.state import RuntimeStateStore

                value = RuntimeStateStore(db, "ws", "conv").get(
                    "goal.resume:goal1"
                )
                self.assertEqual(value["tool_output"], "approved output")
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
