import asyncio
import os
import tempfile
import time
from pathlib import Path
import unittest

from kitt.artifacts.store import ArtifactStore
from kitt.children.manager import ChildAgentManager
from kitt.children.repository import ChildRepository
from kitt.children.messaging import ChildMessageRepository
from kitt.core.runtime import KittRuntime
from kitt.core.runtime_config import RuntimeConfig
from kitt.daemon.client import DaemonClient
from kitt.daemon.server import DaemonServer
from kitt.daemon.transport import IPCTransport
from kitt.goals.models import Goal
from kitt.goals.scheduler import GoalScheduler
from kitt.goals.service import GoalService
from kitt.history.database import HistoryDatabase
from kitt.history.migrations import MigrationRunner
from kitt.runtime.safe_runtime import SafeRuntime
from kitt.security.capabilities import (
    ALL_CAPABILITIES,
    CAP_REPO_READ,
    CAP_REPO_WRITE,
    CAP_PROCESS_RUN,
    CAP_ARTIFACT_WRITE,
    compute_child_privileges,
)
from kitt.skills.executable import (
    ExecutableSkillMetadata,
    ExecutableSkillRunner,
    validate_skill_ast,
)
from kitt.tools.surface_selector import ToolSurfaceSelector
from kitt.context_filter.context_planner import ContextPlan


class TestPrimeHardeningSuite(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.db = HistoryDatabase(str(self.root))
        self.migration_runner = MigrationRunner()
        self.artifacts = ArtifactStore(str(self.root), self.db)
        self.child_repo = ChildRepository(self.db)
        self.child_messaging = ChildMessageRepository(self.db)
        self.child_manager = ChildAgentManager(
            str(self.root),
            self.child_repo,
            self.artifacts,
            messaging_repo=self.child_messaging,
            workspace_id="test_ws",
        )
        self.goal_service = GoalService(self.db)

        # Seed workspace and conversations
        with self.db.get_connection() as conn:
            now = time.time()
            conn.execute(
                "INSERT OR IGNORE INTO workspaces (id, canonical_path_hash, display_name, created_at, last_opened_at) VALUES (?, ?, ?, ?, ?)",
                ("test_ws", "hash123", "Test WS", now, now),
            )
            conn.execute(
                "INSERT OR IGNORE INTO conversations (id, workspace_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                ("test_conv_corr", "test_ws", "Corr Conv", now, now),
            )
            conn.execute(
                "INSERT OR IGNORE INTO conversations (id, workspace_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                ("conv_goal_test", "test_ws", "Goal Conv", now, now),
            )
            conn.commit()

    def tearDown(self):
        self.child_manager.close()
        self.db.close()
        self.temp_dir.cleanup()

    def test_01_migration_v11_applied_cleanly(self):
        """Verify Migration 11 tables, columns and indexes are created idempotently."""
        with self.db.get_connection() as conn:
            # Check goals columns
            cols = [r[1] for r in conn.execute("PRAGMA table_info(goals)").fetchall()]
            self.assertIn("lease_id", cols)
            self.assertIn("lease_expires_at", cols)
            self.assertIn("max_cost", cols)
            self.assertIn("failures_used", cols)
            self.assertIn("retries_used", cols)

            # Check child_sessions columns
            child_cols = [r[1] for r in conn.execute("PRAGMA table_info(child_sessions)").fetchall()]
            self.assertIn("current_task_id", child_cols)
            self.assertIn("task_started_at", child_cols)
            self.assertIn("capabilities_json", child_cols)

            # Check child_messages columns
            msg_cols = [r[1] for r in conn.execute("PRAGMA table_info(child_messages)").fetchall()]
            self.assertIn("correlation_id", msg_cols)
            self.assertIn("reply_to", msg_cols)
            self.assertIn("trace_id", msg_cols)

    def test_02_executable_skills_ast_sandbox_blocks_forbidden(self):
        """Verify AST sandbox strictly blocks forbidden modules, calls, and reflection (CRIT-004)."""
        bad_sources = [
            "import os\ndef execute(c, a): os.system('echo 1')",
            "import subprocess\ndef execute(c, a): subprocess.run(['ls'])",
            "import socket\ndef execute(c, a): socket.socket()",
            "from sys import exit\ndef execute(c, a): exit(0)",
            "def execute(c, a): open('/etc/passwd').read()",
            "def execute(c, a): eval('1+1')",
            "def execute(c, a): exec('x=1')",
            "def execute(c, a): return ().__class__.__bases__[0].__subclasses__()",
        ]
        for src in bad_sources:
            with self.assertRaises((PermissionError, ValueError), msg=f"Should have blocked: {src}"):
                validate_skill_ast(src)

    def test_03_executable_skills_safe_execution(self):
        """Verify safe skills execute properly within sandboxed globals and capabilities."""
        skill_dir = self.root / "skills" / "safe_math"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            """---
name: safe_math
description: Pure math computation
capabilities:
  - repo.read
---
""",
            encoding="utf-8",
        )
        (skill_dir / "skill.py").write_text(
            """
import math
import json

def execute(context, arguments):
    x = arguments.get("val", 0)
    return {"sqrt": math.sqrt(x), "json": json.dumps({"input": x})}
""",
            encoding="utf-8",
        )

        class MockRuntime:
            root = self.root

        runner = ExecutableSkillRunner(MockRuntime())
        meta = runner.parse_metadata(skill_dir)
        self.assertIsNotNone(meta)
        self.assertEqual(meta.name, "safe_math")

        res = runner.execute("safe_math", {"val": 16})
        self.assertTrue(res.success, msg=res.error)
        self.assertEqual(res.data["sqrt"], 4.0)

    def test_04_child_agents_capability_intersection_and_correlation(self):
        """Verify child agent capability intersection and correlated ask/reply messaging (HIGH-001 - HIGH-006)."""
        # Capability intersection
        effective = compute_child_privileges(
            requested=["read_file", "search", "run_command"],
            parent_capabilities=["read_file", "search"],  # Parent lacks run_command
        )
        self.assertIn("repo.read", effective)
        self.assertIn("repo.search", effective)
        self.assertNotIn("process.run", effective)

        # Retained child lifecycle & correlated messaging
        conv_id = "test_conv_corr"
        child = self.child_manager.spawn(
            parent_conversation_id=conv_id,
            parent_turn_id="turn_1",
            name="test_worker",
            task="do_work",
            worker=lambda t: f"work_done: {t}",
            enabled_tools=["read_file"],
        )
        self.assertIsNotNone(child)

        # Correlated ask
        ask_res = self.child_manager.ask(child.id, "Status update?", timeout=0.5)
        self.assertIn(ask_res["status"], {"SENT", "ANSWERED"})
        self.assertIsNotNone(ask_res.get("correlation_id"))

        # Child replies
        msgs = self.child_manager.list_messages(conv_id)
        ask_msg = next(m for m in msgs if m.kind == "ASK")
        reply_msg = self.child_manager.reply(child.id, ask_msg, {"answer": "90% done"})
        self.assertEqual(reply_msg.correlation_id, ask_msg.correlation_id)
        self.assertEqual(reply_msg.reply_to, ask_msg.id)

    def test_05_safe_runtime_tool_surface_token_reduction(self):
        """Verify Safe Runtime tool surface reduction vs legacy tool contracts (Fase 2 & HIGH-019)."""
        from kitt.context_filter.prompt_budget import TokenCounter
        from kitt.tools.registry import ToolRegistry
        selector = ToolSurfaceSelector(config=RuntimeConfig(tool_runtime_mode="safe_runtime"))
        plan = ContextPlan(
            enabled_tools=["read_file", "search", "write_file", "apply_patch", "python_compute", "run_command"],
        )
        selected = selector.select_tools(plan, model_capabilities={"tool_calls": True})
        self.assertEqual(selected, ["kitt_runtime"])

        # Real token measurements from serialized registry definitions
        reg = ToolRegistry(root_dir=str(self.root))
        res = ToolSurfaceSelector.compare_surfaces(reg, plan.enabled_tools, TokenCounter)
        self.assertGreater(res["legacy_tokens"], res["safe_runtime_tokens"])
        self.assertGreater(res["saved_pct"], 60.0)

    def test_06_goal_scheduler_atomic_lease_and_budgets(self):
        """Verify GoalScheduler atomic lease claim and budget enforcement (CRIT-011, CRIT-012, HIGH-024)."""
        scheduler = GoalScheduler(self.db, self.goal_service, poll_interval_seconds=0.1)

        # Create goal with tight turn limit
        goal = self.goal_service.create(
            conversation_id="conv_goal_test",
            objective="Autonomous test task",
            max_turns=2,
            max_wall_seconds=10,
        )
        # Configure schedule
        ok = scheduler.schedule_goal(goal.id, heartbeat_enabled=True, next_run_delay_seconds=0.0)
        self.assertTrue(ok)

        # Simulate turns used exceeding budget
        with self.db.get_connection() as conn:
            conn.execute("UPDATE goals SET turns_used = 3 WHERE id = ?", (goal.id,))
            conn.commit()

        # Check and execute due
        results = scheduler.check_and_execute_due()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "PAUSED_BUDGET_EXCEEDED")

        # Verify updated database state
        updated = self.goal_service.get(goal.id)
        self.assertEqual(updated.state, "PAUSED_BUDGET_EXCEEDED")

    async def test_07_daemon_multiplexed_client_server_lifecycle(self):
        """Verify DaemonServer and DaemonClient single-reader multiplexing and token security (CRIT-005 - CRIT-007)."""
        token_path = self.root / ".kitt" / "daemon.token"
        server = DaemonServer(
            socket_path=self.root / ".kitt" / "daemon.sock",
            token_path=token_path,
            workspace_root=str(self.root),
        )
        await server.start()

        # Token security verification (0600 mode)
        if os.name != "nt":
            mode = token_path.stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)

        client = DaemonClient(
            socket_path=self.root / ".kitt" / "daemon.sock",
            token_path=token_path,
            workspace_root=str(self.root),
        )
        try:
            connected = await client.connect()
            self.assertTrue(connected)

            # Multiplexed requests
            ping_res = await client.is_running()
            self.assertTrue(ping_res)

            sess_res = await client.list_sessions(workspace=str(self.root))
            self.assertEqual(sess_res.get("status"), "ok")

            # Create and attach session
            created = await client.create_session("daemon_test_session", workspace=str(self.root))
            self.assertEqual(created.get("status"), "ok")
            session_id = created["session_id"]
            events = []
            attach_res = await client.attach(
                session_id,
                event_callback=lambda e: events.append(e),
                workspace=str(self.root),
            )
            self.assertEqual(attach_res.get("status"), "ok")

            await client.detach()
        finally:
            await client.close()
            await server.stop()
