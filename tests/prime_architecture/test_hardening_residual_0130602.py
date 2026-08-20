from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from kitt.core.pending_action import PendingAction, canonical_args_digest
from kitt.core.runtime import KittRuntime
from kitt.core.turn_events import TurnCompleted, TurnFailed
from kitt.extensions.manifest import parse_manifest_file
from kitt.extensions.plugins.loader import PluginLoader
from kitt.extensions.plugins.security import (
    PluginStateStore,
    PluginTrustStore,
    prepare_trusted_plugin_snapshot,
)
from kitt.goals.scheduler import GoalScheduler
from kitt.security.capabilities import CAP_REPO_READ, CAP_REPO_WRITE
from kitt.security.context import ExecutionSecurityContext


class TestGoalFencePropagation(unittest.TestCase):
    def test_stale_goal_blocks_derived_skill_and_child(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "visible.txt").write_text("ok", encoding="utf-8")
            with KittRuntime.build(str(root)) as runtime:
                conversation = runtime.history.new_conversation("goal-fence")
                goal = runtime.goals.create(
                    conversation["id"],
                    "read",
                    capabilities=[CAP_REPO_READ],
                    max_wall_seconds=60,
                )
                scheduler = GoalScheduler(runtime.database, runtime.goals)
                lease_id = scheduler._claim(goal.id)
                self.assertIsNotNone(lease_id)

                goal_ctx = ExecutionSecurityContext(
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
                    fencing_subject_type="GOAL",
                    fencing_subject_id=goal.id,
                )
                skill_ctx = goal_ctx.derive_skill_context(
                    "skill:test",
                    requested_capabilities={CAP_REPO_READ},
                )
                child_ctx = goal_ctx.derive_child_context(
                    "child:test",
                    requested_capabilities={CAP_REPO_READ},
                )
                self.assertEqual(child_ctx.fencing_subject_id, goal.id)

                ok = runtime.registry.execute_tool(
                    "read_file",
                    {"path": "visible.txt"},
                    turn_id="turn",
                    conversation_id=conversation["id"],
                    workspace_id=runtime.workspace_id,
                    origin="SCHEDULE",
                    security_context=skill_ctx,
                )
                self.assertTrue(ok.success, ok.error)

                with runtime.database.get_connection() as conn:
                    conn.execute(
                        "UPDATE goals SET lease_id='stolen', lease_owner_id='other' WHERE id=?",
                        (goal.id,),
                    )

                for ctx in (skill_ctx, child_ctx):
                    denied = runtime.registry.execute_tool(
                        "read_file",
                        {"path": "visible.txt"},
                        turn_id="turn",
                        conversation_id=conversation["id"],
                        workspace_id=runtime.workspace_id,
                        origin="SCHEDULE",
                        security_context=ctx,
                    )
                    self.assertFalse(denied.success)
                    self.assertIn("lease", (denied.error or "").lower())

    def test_child_of_child_preserves_goal_subject(self):
        parent = ExecutionSecurityContext(
            workspace_id="ws",
            conversation_id="conv",
            turn_id="turn",
            origin="SCHEDULE",
            principal_type="GOAL",
            principal_id="goal1",
            capabilities=frozenset({CAP_REPO_READ}),
            trace_id="trace",
            fencing_token="lease1",
            fencing_owner_id="owner1",
            fencing_subject_type="GOAL",
            fencing_subject_id="goal1",
        )
        child = parent.derive_child_context("child1", {CAP_REPO_READ})
        grandchild = child.derive_child_context("child2", {CAP_REPO_READ})
        self.assertEqual(grandchild.fencing_subject_type, "GOAL")
        self.assertEqual(grandchild.fencing_subject_id, "goal1")

    def test_retained_child_persists_goal_subject(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with KittRuntime.build(temp_dir) as runtime:
                conversation = runtime.history.new_conversation("retained")
                goal_ctx = ExecutionSecurityContext(
                    workspace_id=runtime.workspace_id,
                    conversation_id=conversation["id"],
                    turn_id="turn",
                    origin="SCHEDULE",
                    principal_type="GOAL",
                    principal_id="goal1",
                    capabilities=frozenset({CAP_REPO_READ}),
                    trace_id="trace",
                    fencing_token="lease1",
                    fencing_owner_id="owner1",
                    fencing_subject_type="GOAL",
                    fencing_subject_id="goal1",
                )
                child = runtime.children.spawn(
                    parent_conversation_id=conversation["id"],
                    parent_turn_id="turn",
                    name="retained",
                    task="inspect",
                    worker=lambda _task: "done",
                    security_context=goal_ctx,
                    allowed_tools=["read_file"],
                )
                runtime.children.wait(child.id, timeout=5.0)
                stored = runtime.children.inspect(child.id)
                self.assertEqual(stored.security_context["fencing_subject_id"], "goal1")
                self.assertEqual(stored.security_context["fencing_subject_type"], "GOAL")


class TestGoalApprovalResume(unittest.TestCase):
    def test_approval_resume_clears_old_lease_and_grant_is_single_use(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "a.txt"
            target.write_text("old\n", encoding="utf-8")
            with KittRuntime.build(str(root)) as runtime:
                conversation = runtime.history.new_conversation("approval")
                goal = runtime.goals.create(
                    conversation["id"],
                    "patch",
                    capabilities=[CAP_REPO_WRITE],
                    max_wall_seconds=60,
                )
                scheduler = GoalScheduler(runtime.database, runtime.goals)
                old_lease = scheduler._claim(goal.id)
                self.assertIsNotNone(old_lease)
                with runtime.database.get_connection() as conn:
                    conn.execute(
                        "UPDATE goals SET state='WAITING_APPROVAL' WHERE id=?",
                        (goal.id,),
                    )

                goal_ctx = ExecutionSecurityContext(
                    workspace_id=runtime.workspace_id,
                    conversation_id=conversation["id"],
                    turn_id="turn",
                    origin="SCHEDULE",
                    principal_type="GOAL",
                    principal_id=goal.id,
                    capabilities=frozenset({CAP_REPO_WRITE}),
                    trace_id="trace",
                    fencing_token=old_lease,
                    fencing_owner_id=scheduler.worker_id,
                    fencing_subject_type="GOAL",
                    fencing_subject_id=goal.id,
                )
                args = {
                    "patch": "a.txt\n<<<<<<< SEARCH\nold\n=======\nnew\n>>>>>>> REPLACE"
                }
                action_hash = runtime.registry.policy.generate_action_hash(
                    "apply_patch", args
                )
                approval_id = "req_turn"
                runtime.approval.register_request(
                    "turn",
                    conversation["id"],
                    runtime.workspace_id,
                    action_hash,
                    approval_id,
                    tool_name="apply_patch",
                )
                runtime.history.repo.save_message(
                    conversation["id"],
                    "turn",
                    "user",
                    "approve patch",
                )
                grant = runtime.approval.issue_grant(
                    "turn",
                    conversation["id"],
                    runtime.workspace_id,
                    action_hash,
                    approval_id=approval_id,
                )
                self.assertIsNotNone(grant)
                pending = PendingAction(
                    id="pa_turn",
                    approval_request_id=approval_id,
                    turn_id="turn",
                    conversation_id=conversation["id"],
                    workspace_id=runtime.workspace_id,
                    tool_name="apply_patch",
                    normalized_args=args,
                    action_hash=action_hash,
                    source_response_sha256=canonical_args_digest(args),
                    affected_paths=["a.txt"],
                    before_hashes={
                        "a.txt": hashlib.sha256(target.read_bytes()).hexdigest()
                    },
                    created_at=time.time(),
                    expires_at=time.time() + 60,
                    state="pending",
                    security_context=goal_ctx.to_dict(),
                )
                runtime.processor.pending_actions["turn"] = pending
                runtime.history.repo.save_pending_action(pending)

                events = list(runtime.processor.continue_turn("turn", grant))
                self.assertTrue(any(isinstance(ev, TurnCompleted) for ev in events))
                self.assertEqual(target.read_text(encoding="utf-8"), "new\n")

                resumed = runtime.goals.get(goal.id)
                self.assertEqual(resumed.state, "ACTIVE")
                self.assertIsNone(resumed.lease_id)
                self.assertIsNone(resumed.lease_owner_id)

                new_lease = scheduler._claim(goal.id)
                self.assertIsNotNone(new_lease)
                self.assertNotEqual(new_lease, old_lease)

                failed = next(runtime.processor.continue_turn("turn", grant))
                self.assertIsInstance(failed, TurnFailed)


class TestRuntimeLifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_start_and_aclose_are_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = KittRuntime.build(temp_dir)
            await runtime.start()
            await runtime.start()
            await runtime.aclose()
            await runtime.aclose()

    async def test_close_inside_running_loop_requires_aclose(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = KittRuntime.build(temp_dir)
            await runtime.start()
            with self.assertRaises(RuntimeError):
                runtime.close()
            await runtime.aclose()

    async def test_start_failure_rolls_back_started_flag(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = KittRuntime.build(temp_dir)
            calls = []

            async def broken_start():
                calls.append("start")
                raise RuntimeError("boom")

            async def stop_after_failure():
                calls.append("stop")

            runtime.extensions.start = broken_start
            runtime.extensions.stop = stop_after_failure
            with self.assertRaises(RuntimeError):
                await runtime.start()
            self.assertFalse(runtime._started)
            self.assertIn("stop", calls)
            await runtime.aclose()


class TestPluginSnapshotAndStores(unittest.IsolatedAsyncioTestCase):
    def _create_plugin(self, root: Path, name: str, body: str = "return None\n") -> Path:
        plugin = root / ".kitt" / "plugins" / name
        plugin.mkdir(parents=True)
        (plugin / "plugin.py").write_text(
            "def setup(ctx):\n    " + body,
            encoding="utf-8",
        )
        (plugin / "plugin.toml").write_text(
            "\n".join(
                [
                    f'name = "{name}"',
                    'version = "1.0.0"',
                    'api_version = "1"',
                    'entrypoint = "plugin:setup"',
                    "permissions = []",
                    "trusted_in_process = true",
                ]
            ),
            encoding="utf-8",
        )
        return plugin

    async def test_snapshot_cache_reused_by_digest_and_mutation_blocks_load(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plugin = self._create_plugin(root, "demo")
            manifest = parse_manifest_file(plugin / "plugin.toml")
            trust = PluginTrustStore(root, path=root / "trust.json")
            digest = trust.grant(manifest)
            snap1 = prepare_trusted_plugin_snapshot(manifest, digest, root)
            snap2 = prepare_trusted_plugin_snapshot(manifest, digest, root)
            self.assertEqual(snap1, snap2)

            loader = PluginLoader(workspace_root=str(root), trust_store=trust)
            instance = await loader.load_async(manifest)
            self.assertEqual(instance.state.value, "LOADED")

            (plugin / "plugin.py").write_text(
                "def setup(ctx):\n    return 'mutated'\n",
                encoding="utf-8",
            )
            with self.assertRaises(Exception):
                await loader.load_async(manifest)

    async def test_symlink_plugin_is_blocked(self):
        if sys.platform == "win32":
            self.skipTest("symlink permissions vary on Windows")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plugin = self._create_plugin(root, "linkdemo")
            target = root / "outside.txt"
            target.write_text("bad", encoding="utf-8")
            (plugin / "linked.txt").symlink_to(target)
            manifest = parse_manifest_file(plugin / "plugin.toml")
            trust = PluginTrustStore(root, path=root / "trust.json")
            with self.assertRaises(Exception):
                trust.grant(manifest)

    def test_trust_and_state_updates_survive_concurrent_processes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plugin_one = self._create_plugin(root, "one")
            plugin_two = self._create_plugin(root, "two")
            trust_path = root / "trust.json"
            state_path = root / "state.json"
            code = textwrap.dedent(
                """
                import sys
                from pathlib import Path
                from kitt.extensions.manifest import parse_manifest_file
                from kitt.extensions.plugins.security import PluginStateStore, PluginTrustStore

                root = Path(sys.argv[1])
                manifest = parse_manifest_file(Path(sys.argv[2]))
                trust = PluginTrustStore(root, path=Path(sys.argv[3]))
                state = PluginStateStore(root, path=Path(sys.argv[4]))
                trust.grant(manifest)
                state.set_enabled(manifest.name, True)
                """
            )
            procs = [
                subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        code,
                        str(root),
                        str(plugin_one / "plugin.toml"),
                        str(trust_path),
                        str(state_path),
                    ]
                ),
                subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        code,
                        str(root),
                        str(plugin_two / "plugin.toml"),
                        str(trust_path),
                        str(state_path),
                    ]
                ),
            ]
            for proc in procs:
                self.assertEqual(proc.wait(timeout=20), 0)

            trust_data = json.loads(trust_path.read_text(encoding="utf-8"))
            plugins = trust_data["workspaces"][PluginTrustStore(root, path=trust_path).workspace_key]["plugins"]
            self.assertIn("workspace:one", plugins)
            self.assertIn("workspace:two", plugins)
            enabled, disabled = PluginStateStore(root, path=state_path).load()
            self.assertEqual(disabled, set())
            self.assertIn("one", enabled)
            self.assertIn("two", enabled)


if __name__ == "__main__":
    unittest.main()
