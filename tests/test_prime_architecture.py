from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from kitt.children.manager import ChildAgentManager
from kitt.children.messaging import ChildMessageRepository
from kitt.children.repository import ChildRepository
from kitt.core.runtime import KittRuntime
from kitt.core.runtime_config import RuntimeConfig
from kitt.daemon.client import DaemonClient
from kitt.daemon.protocol import DaemonEvent
from kitt.daemon.server import DaemonServer
from kitt.goals.scheduler import GoalScheduler
from kitt.goals.service import GoalService
from kitt.history.database import HistoryDatabase
from kitt.runtime.handles import ContextHandleResolver
from kitt.runtime.safe_runtime import SafeRuntime
from kitt.runtime.state import RuntimeStateStore
from kitt.security.capabilities import (
    ALL_CAPABILITIES,
    CAP_ARTIFACT_READ,
    CAP_ARTIFACT_WRITE,
    CAP_CHILD_SPAWN,
    CAP_PROCESS_RUN,
    CAP_REPO_READ,
    CAP_REPO_SEARCH,
    CAP_REPO_WRITE,
    compute_child_privileges,
    validate_capabilities,
)
from kitt.skills.executable import (
    ExecutableSkillMetadata,
    ExecutableSkillRunner,
    SkillExecutionContext,
)


class TestPrimeArchitecture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = RuntimeConfig(history_enabled=True, persistence_enabled=True)
        self.runtime = KittRuntime.build(str(self.root), config=self.config)

    def tearDown(self):
        self.runtime.close()
        self.tmp.cleanup()

    # --- 1. Capability model & Privilege inheritance ---
    def test_capability_validation_and_privilege_inheritance(self):
        valid = validate_capabilities([CAP_REPO_READ, CAP_PROCESS_RUN])
        self.assertEqual(valid, {CAP_REPO_READ, CAP_PROCESS_RUN})

        with self.assertRaises(ValueError):
            validate_capabilities(["unknown.capability"])

        # Child cannot escalate beyond parent capabilities
        parent_caps = [CAP_REPO_READ, CAP_REPO_SEARCH]
        requested = [CAP_REPO_READ, CAP_PROCESS_RUN]  # requests process.run which parent lacks
        effective = compute_child_privileges(requested, parent_caps)
        self.assertEqual(effective, {CAP_REPO_READ})
        self.assertNotIn(CAP_PROCESS_RUN, effective)

    # --- 2. Safe Runtime & Path Escape Blocking ---
    def test_safe_runtime_and_path_escape_blocked(self):
        test_file = self.root / "module.py"
        test_file.write_text("def target_fn():\n    return 42\n")

        safe_rt = SafeRuntime(
            workspace_root=self.root,
            workspace_id=self.runtime.workspace_id,
            conversation_id="conv_test",
            tool_registry=self.runtime.registry,
            repository_index=self.runtime.repository_index,
            artifact_store=self.runtime.artifacts,
            child_manager=self.runtime.children,
            goal_service=self.runtime.goals,
            db=self.runtime.database,
        )

        # Read allowed
        res = safe_rt.execute("repo.read", {"path": "module.py", "start_line": 1, "end_line": 2}, effective_capabilities={"repo.read"})
        self.assertTrue(res.success)
        self.assertIn("target_fn", str(res.data))

        # Path traversal blocked
        res_esc = safe_rt.execute("repo.read", {"path": "../../etc/passwd"}, effective_capabilities={"repo.read"})
        self.assertFalse(res_esc.success)

    # --- 3. RuntimeState limits & TTL ---
    def test_runtime_state_limits_and_ttl(self):
        conv = self.runtime.history.get_or_create_active()
        conv_id = conv["id"]
        state_store = RuntimeStateStore(self.runtime.database, self.runtime.workspace_id, conv_id)
        state_store.set("key1", {"value": 123}, ttl_seconds=100)
        self.assertEqual(state_store.get("key1"), {"value": 123})

        # List keys
        keys = state_store.list_keys()
        self.assertEqual(len(keys), 1)
        self.assertEqual(keys[0]["key"], "key1")

        # Expired TTL
        state_store.set("expired_key", "gone", ttl_seconds=-1)
        self.assertIsNone(state_store.get("expired_key"))

        # Oversized entry rejection
        oversized = "x" * (70 * 1024)
        with self.assertRaises(ValueError):
            state_store.set("big_key", oversized)

    # --- 4. Context handles resolution ---
    def test_context_handle_resolution(self):
        f = self.root / "sample.txt"
        f.write_text("line1\nline2\nline3\nline4\n")

        resolver = ContextHandleResolver(
            self.root,
            repository_index=self.runtime.repository_index,
            artifact_store=self.runtime.artifacts,
            child_manager=self.runtime.children,
            goal_service=self.runtime.goals,
        )

        res = resolver.resolve("ctx:file:sample.txt:2-3")
        self.assertEqual(res["kind"], "file_slice")
        self.assertEqual(res["start_line"], 2)
        self.assertEqual(res["end_line"], 3)
        self.assertIn("line2", res["content"])

        # Path traversal handle blocked
        with self.assertRaises(ValueError):
            resolver.resolve("ctx:file:../../secret.txt:1-10")

    # --- 5. Executable Skills load, capabilities & cycle detection ---
    def test_executable_skills_capabilities_and_cycles(self):
        skills_dir = self.root / ".kitt" / "skills" / "calc_skill"
        skills_dir.mkdir(parents=True, exist_ok=True)

        (skills_dir / "SKILL.md").write_text(
            """---
name: calc_skill
description: Performs safe calculation
capabilities:
  - repo.read
---
# Instructions
"""
        )
        (skills_dir / "skill.py").write_text(
            """
def execute(ctx, args):
    content = ctx.read_file("test.txt")
    return {"length": len(content)}
"""
        )

        (self.root / "test.txt").write_text("hello skill")

        runner = ExecutableSkillRunner(self.runtime)
        # Point runner root
        self.runtime.root = self.root
        meta = runner.parse_metadata(skills_dir)
        self.assertTrue(meta.is_executable)
        self.assertEqual(meta.capabilities, [CAP_REPO_READ])

        # Test capability guard (cannot run process.run because not declared)
        exec_ctx = SkillExecutionContext(meta, self.runtime)
        with self.assertRaises(PermissionError):
            exec_ctx.run_command("ls")

        # Test cycle detection
        with self.assertRaises(RuntimeError):
            exec_ctx.call_skill("calc_skill", {})

    # --- 6. Retained Agents & Parent-Child Messaging ---
    def test_retained_agents_and_messaging(self):
        conv = self.runtime.history.repo.create_conversation(self.runtime.workspace_id, title="Test Conv")
        conv_id = conv["id"]
        parent_turn_id = "turn_1"

        child = self.runtime.children.spawn(
            parent_conversation_id=conv_id,
            parent_turn_id=parent_turn_id,
            name="specialist_1",
            task="initial analysis",
            worker=lambda t: f"done: {t}",
        )

        self.runtime.children.wait(child.id, timeout=5.0)
        c_done = self.runtime.children.inspect(child.id)
        self.assertEqual(c_done.state, "COMPLETED")

        # Retain child
        retained_ok = self.runtime.children.retain(child.id)
        self.assertTrue(retained_ok)
        c_ret = self.runtime.children.inspect(child.id)
        self.assertEqual(c_ret.state, "RETAINED")

        # Send message
        msg = self.runtime.children.send_message(
            conversation_id=conv_id,
            parent_id=conv_id,
            child_id=child.id,
            sender_id=conv_id,
            recipient_id=child.id,
            payload={"instruction": "check memory"},
        )
        self.assertEqual(msg.status, "SENT")

        messages = self.runtime.children.list_messages(conv_id, child_id=child.id)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].payload, {"instruction": "check memory"})

        # Assign new task to retained child
        c_assigned = self.runtime.children.assign_task(
            child_id=child.id,
            task="follow up task",
            worker=lambda t: f"follow up done: {t}",
        )
        self.runtime.children.wait(child.id, timeout=5.0)
        c_after = self.runtime.children.inspect(child.id)
        self.assertEqual(c_after.state, "COMPLETED")

    # --- 7. Goal Scheduler & Budget Enforcement ---
    def test_goal_scheduler_budget_and_execution(self):
        conv = self.runtime.history.repo.create_conversation(self.runtime.workspace_id, title="Goal Conv")
        conv_id = conv["id"]

        goal = self.runtime.goals.create(
            conversation_id=conv_id,
            objective="Autonomous refactoring",
            token_budget=1000,
            max_turns=2,
            max_wall_seconds=100,
        )

        scheduler = GoalScheduler(
            db=self.runtime.database,
            goal_service=self.runtime.goals,
            runtime_step_executor=lambda goal: {"status": "SUCCEEDED"},
        )

        scheduler.schedule_goal(goal.id, recurrence="60", heartbeat_enabled=True, next_run_delay_seconds=0.0)

        # 1st run succeeds
        results = scheduler.check_and_execute_due()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "SUCCEEDED")

        # Update tokens_used on active goal to exceed token budget (e.g. from background attribution)
        with self.runtime.database.get_connection() as conn:
            conn.execute("UPDATE goals SET tokens_used = 1200, next_run_at = 0 WHERE id = ?", (goal.id,))
            conn.commit()

        # Next scheduler run detects budget exceeded -> PAUSED_BUDGET_EXCEEDED
        results_budget = scheduler.check_and_execute_due()
        self.assertEqual(len(results_budget), 1)
        self.assertEqual(results_budget[0]["status"], "PAUSED_BUDGET_EXCEEDED")

        g_paused = self.runtime.goals.get(goal.id)
        self.assertEqual(g_paused.state, "PAUSED_BUDGET_EXCEEDED")

    # --- 8. Legacy Tool Compatibility & Safe Runtime tool ---
    def test_legacy_tools_and_safe_runtime_dispatch(self):
        from kitt.security.context import ExecutionSecurityContext
        defs = self.runtime.registry.get_tool_definitions()
        tool_names = {t["name"] for t in defs}
        self.assertIn("kitt_runtime", tool_names)
        self.assertIn("read_file", tool_names)
        self.assertIn("search", tool_names)
        self.assertIn("write_file", tool_names)

        # Execute safe runtime tool via registry
        conv = self.runtime.history.get_or_create_active()
        conv_id = conv["id"]
        (self.root / "hello.py").write_text("print('hello safe runtime')")
        sec_ctx = ExecutionSecurityContext.create_user_context(
            workspace_id=self.runtime.workspace_id,
            conversation_id=conv_id,
            turn_id="turn_safe",
            capabilities={"repo.read"},
        )
        res = self.runtime.registry.execute_tool(
            "kitt_runtime",
            {"operation": "repo.read", "arguments": {"path": "hello.py"}},
            turn_id="turn_safe",
            conversation_id=conv_id,
            workspace_id=self.runtime.workspace_id,
            security_context=sec_ctx,
        )
        self.assertTrue(res.success)
        self.assertIn("hello safe runtime", res.output)


class TestDaemonServerClient(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.socket_path = self.root / "test_daemon.sock"
        self.token_path = self.root / "test_daemon.token"
        self.server = DaemonServer(
            socket_path=self.socket_path,
            token_path=self.token_path,
            workspace_root=str(self.root),
        )
        await self.server.start()
        self.client = DaemonClient(
            socket_path=self.socket_path,
            token_path=self.token_path,
        )

    async def asyncTearDown(self):
        await self.client.close()
        await self.server.stop()
        self.tmp.cleanup()

    async def test_daemon_auth_and_session_list(self):
        connected = await self.client.connect()
        self.assertTrue(connected)
        self.assertTrue(await self.client.is_running())

        sessions = await self.client.list_sessions(workspace=str(self.root))
        self.assertEqual(sessions.get("status"), "ok")
        self.assertIn("sessions", sessions)

    async def test_daemon_attach_and_event_replay(self):
        await self.client.connect()

        # Seed events in server
        rt = self.server._get_or_create_runtime(str(self.root))
        conv = rt.history.new_conversation("daemon_session_1")
        session_id = conv["id"]
        self.server.record_event(rt.database, session_id, "SessionCreated", {"info": "init"})
        self.server.record_event(rt.database, session_id, "TurnStep", {"step": 1})

        events_received = []

        def on_event(evt):
            events_received.append(evt)

        attach_res = await self.client.attach(session_id, last_sequence=0, event_callback=on_event, workspace=str(self.root))
        self.assertEqual(attach_res.get("status"), "ok")
        self.assertEqual(len(attach_res.get("events", [])), 2)

        # Broadcast live event
        self.server.record_event(rt.database, session_id, "LiveStep", {"step": 2})
        await asyncio.sleep(0.1)

        self.assertTrue(any(e.event_type == "LiveStep" for e in events_received))
