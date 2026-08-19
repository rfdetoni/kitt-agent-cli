import tempfile
import time
import unittest
from pathlib import Path

from kitt.core.runtime import KittRuntime
from kitt.security.capabilities import CAP_REPO_READ, CAP_REPO_WRITE, compute_child_privileges


class TestRetainedAgentsLifecycle(unittest.TestCase):
    """Rigorous tests for Retained Agent state machine, task assignment, and correlated messaging."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.runtime = KittRuntime.build(str(self.root))
        self.conv = self.runtime.history.new_conversation("Retained Agent Main")

    def tearDown(self):
        self.runtime.close()
        self.temp_dir.cleanup()

    def test_01_retained_agent_lifecycle_and_task_reassignment(self):
        """Verify child agent can be retained and sequentially assigned new tasks with real results."""
        cm = self.runtime.children

        # 1. Spawn child with initial task
        child = cm.spawn(
            parent_conversation_id=self.conv["id"],
            parent_turn_id="turn_001",
            name="specialist_01",
            task="Task 1 initial analysis",
            worker=lambda t: f"Result for {t}",
            allowed_tools=["read_file"],
        )
        self.assertIsNotNone(child)

        # Wait for task 1 completion
        c_done = cm.wait(child.id, timeout=5.0)
        self.assertEqual(c_done.state, "COMPLETED")
        self.assertIsNotNone(c_done.result_artifact_id)

        # 2. Retain agent for subsequent specialist tasks
        retained = cm.retain(child.id)
        self.assertTrue(retained)
        inspected = cm.inspect(child.id)
        self.assertEqual(inspected.state, "RETAINED")

        # 3. Assign task 2 to the same retained agent
        updated = cm.assign_task(
            child.id,
            task="Task 2 deep inspection",
            worker=lambda t: f"Result for {t}",
        )
        self.assertEqual(updated.state, "QUEUED")

        # Wait for task 2 completion
        c_done2 = cm.wait(child.id, timeout=5.0)
        self.assertEqual(c_done2.state, "COMPLETED")

        # Verify artifacts were created for both executions
        art = self.runtime.artifacts.get(c_done2.result_artifact_id)
        self.assertIsNotNone(art)
        self.assertIn("Task 2 deep inspection", self.runtime.artifacts.read_text(art.id))

    def test_02_correlated_messaging_between_parent_and_child(self):
        """Verify structured ask/reply correlated messaging between parent and child agents."""
        cm = self.runtime.children
        child = cm.spawn(
            parent_conversation_id=self.conv["id"],
            parent_turn_id="turn_001",
            name="researcher",
            task="Research architecture",
            worker=lambda t: "done",
        )
        cm.wait(child.id, timeout=5.0)

        # Parent sends ASK message with correlation ID
        msg1 = cm.send_message(
            conversation_id=self.conv["id"],
            parent_id=self.conv["id"],
            child_id=child.id,
            sender_id=self.conv["id"],
            recipient_id=child.id,
            payload={"question": "What files were analyzed?"},
            kind="ASK",
            correlation_id="corr_12345",
        )
        self.assertEqual(msg1.correlation_id, "corr_12345")
        self.assertEqual(msg1.kind, "ASK")

        # Child replies with matching correlation ID
        msg2 = cm.send_message(
            conversation_id=self.conv["id"],
            parent_id=self.conv["id"],
            child_id=child.id,
            sender_id=child.id,
            recipient_id=self.conv["id"],
            payload={"answer": "src/module.py"},
            kind="REPLY",
            correlation_id="corr_12345",
            reply_to=msg1.id,
        )
        self.assertEqual(msg2.correlation_id, "corr_12345")
        self.assertEqual(msg2.reply_to, msg1.id)

        # History query verifies message chain
        msgs = cm.messaging.list_messages(self.conv["id"], child_id=child.id)
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0].id, msg1.id)
        self.assertEqual(msgs[1].id, msg2.id)

    def test_03_capability_containment_and_non_escalation(self):
        """Verify child agent derives strictly intersected capabilities and cannot escalate."""
        parent_caps = {CAP_REPO_READ}  # Parent only has READ

        # Child requests WRITE and READ
        requested_tools = ["read_file", "write_file", "apply_patch"]
        effective = compute_child_privileges(requested_tools, parent_caps, {CAP_REPO_READ, CAP_REPO_WRITE})

        # Effective capabilities must ONLY be CAP_REPO_READ
        self.assertIn(CAP_REPO_READ, effective)
        self.assertNotIn(CAP_REPO_WRITE, effective)
        self.assertEqual(effective, {CAP_REPO_READ})
