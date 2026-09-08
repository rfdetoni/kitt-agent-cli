import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from kitt.core.turn_events import MetricsRecorded, ToolCompleted, ToolStarted, TurnCompleted
from kitt.goals.executor import GoalStepExecutor
from kitt.goals.review import ADVERSARIAL_REVIEW_PREFIX, AdversarialCodeReviewer
from kitt.tools.registry import ToolResult


ALL_AREAS = [
    "correctness", "security", "concurrency", "performance",
    "resource_management", "data_integrity", "architecture",
    "maintainability", "tests", "compatibility",
]
CORE_AREAS = ["correctness", "security", "maintainability", "tests"]


class _State:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, ttl_seconds=None):
        self.values[key] = value
        return True

    def delete(self, key):
        return self.values.pop(key, None) is not None


class _Processor:
    def __init__(self):
        self.prompts = []
        self.calls = 0

    def run_turn(self, command):
        self.calls += 1
        self.prompts.append(command.prompt)
        yield ToolStarted(tool_name="write_file", args={"path": "service.py"}, call_id=f"write-{self.calls}")
        yield ToolCompleted(tool_name="write_file", success=True, output="ok", call_id=f"write-{self.calls}")
        yield MetricsRecorded(input_tokens=10, output_tokens=5, estimated_usd=0.01)
        yield TurnCompleted(response="implementation finished")


class _Registry:
    def __init__(self):
        self.process_runner = SimpleNamespace(max_output_bytes=262144)
        self.parser = SimpleNamespace(parse=lambda _patch: [])

    def execute_tool(self, tool_name, args, **_kwargs):
        if tool_name == "git_status":
            return ToolResult(True, " M service.py\n")
        if tool_name == "git_diff":
            return ToolResult(
                True,
                """diff --git a/service.py b/service.py
--- a/service.py
+++ b/service.py
@@ -1 +1,2 @@
+def load_user(user_id):
+    return cache[user_id]
""",
            )
        if tool_name == "read_file":
            return ToolResult(True, "def load_user(user_id):\n    return cache[user_id]\n")
        return ToolResult(False, "", f"unexpected tool {tool_name}")


class TestGoalAdversarialReviewIntegration(unittest.TestCase):
    def test_review_rejects_then_feedback_drives_next_iteration_to_approval(self):
        state = _State()
        processor = _Processor()
        registry = _Registry()
        published = []
        runtime = SimpleNamespace(
            database=object(),
            workspace_id="ws",
            canonical_root=".",
            processor=processor,
            registry=registry,
            policy=SimpleNamespace(evaluate_tool=lambda *_a, **_k: "ALLOW"),
            goals=SimpleNamespace(record_gate_result=lambda *_a, **_k: None),
            events=SimpleNamespace(publish=lambda name, payload: published.append((name, payload))),
        )
        goal = SimpleNamespace(
            id="goal_review",
            conversation_id="conv",
            objective="Implement cached user lookup",
            success_criteria=[],
            gates=[],
            capabilities=["repo.read", "repo.write"],
        )
        review_responses = [
            {
                "status": "CHANGES_REQUIRED",
                "summary": "Happy path only.",
                "reviewed_areas": CORE_AREAS,
                "findings": [
                    {
                        "severity": "HIGH",
                        "confidence": 0.95,
                        "category": "correctness",
                        "title": "Cache miss crashes",
                        "location": "service.py:load_user",
                        "problem": "Direct indexing assumes the key exists.",
                        "impact": "Uncached users raise KeyError.",
                        "required_change": "Handle a cache miss and load from the source.",
                        "evidence": "return cache[user_id]",
                        "required": True,
                    }
                ],
                "approval_evidence": "",
            },
            {
                "status": "APPROVED",
                "summary": "Required defect corrected.",
                "reviewed_areas": ALL_AREAS,
                "findings": [],
                "approval_evidence": "Rechecked the previously failing cache-miss path and regression-test expectations; no required production defect remains in the supplied change.",
            },
        ]

        def reviewer_factory(_runtime, _goal, _state, _usage):
            payload = review_responses.pop(0)
            return AdversarialCodeReviewer(
                lambda *_: f"{ADVERSARIAL_REVIEW_PREFIX} {json.dumps(payload)}"
            )

        executor = GoalStepExecutor(lambda: runtime, reviewer_factory=reviewer_factory)
        with patch("kitt.goals.executor.RuntimeStateStore", return_value=state):
            first = executor(goal, lease_id="lease", lease_owner_id="worker")
            self.assertEqual(first["status"], "INCOMPLETE")
            self.assertEqual(first["review"]["status"], "CHANGES_REQUIRED")
            completion_state = next(
                value for key, value in state.values.items() if key.startswith("goal.completion:")
            )
            self.assertEqual(completion_state["review_paths"], ["service.py"])
            self.assertIn("Cache miss crashes", completion_state["feedback"])

            second = executor(goal, lease_id="lease", lease_owner_id="worker")
            self.assertEqual(second["status"], "SUCCEEDED")
            self.assertEqual(second["review"]["status"], "APPROVED")
            self.assertIn("Previous verification failed", processor.prompts[1])
            self.assertIn("Cache miss crashes", processor.prompts[1])

        self.assertEqual(review_responses, [])
        self.assertEqual(
            [name for name, _ in published].count("GoalAdversarialReviewCompleted"),
            2,
        )

    def test_approved_resume_paths_are_still_reviewed(self):
        state = _State()
        state.values["goal.resume:goal_review"] = {
            "tool_output": "approved write completed",
            "affected_paths": ["service.py"],
        }

        class ResumeProcessor(_Processor):
            def run_turn(self, command):
                self.prompts.append(command.prompt)
                yield MetricsRecorded(input_tokens=3, output_tokens=2, estimated_usd=0.0)
                yield TurnCompleted(response="remaining work complete")

        processor = ResumeProcessor()
        registry = _Registry()
        runtime = SimpleNamespace(
            database=object(),
            workspace_id="ws",
            canonical_root=".",
            processor=processor,
            registry=registry,
            policy=SimpleNamespace(evaluate_tool=lambda *_a, **_k: "ALLOW"),
            goals=SimpleNamespace(record_gate_result=lambda *_a, **_k: None),
            events=SimpleNamespace(publish=lambda *_a, **_k: None),
        )
        goal = SimpleNamespace(
            id="goal_review",
            conversation_id="conv",
            objective="Implement cached user lookup",
            success_criteria=[],
            gates=[],
            capabilities=["repo.read", "repo.write"],
        )
        calls = []

        def reviewer_factory(_runtime, _goal, _state, _usage):
            def approve(_system, user):
                calls.append(user)
                payload = {
                    "status": "APPROVED",
                    "summary": "Approved action is reviewable and acceptable.",
                    "reviewed_areas": ALL_AREAS,
                    "findings": [],
                    "approval_evidence": "Reviewed the approved mutation snapshot, including cache access and regression risk; no required defect remains.",
                }
                return f"{ADVERSARIAL_REVIEW_PREFIX} {json.dumps(payload)}"
            return AdversarialCodeReviewer(approve)

        executor = GoalStepExecutor(lambda: runtime, reviewer_factory=reviewer_factory)
        with patch("kitt.goals.executor.RuntimeStateStore", return_value=state):
            result = executor(goal, lease_id="lease", lease_owner_id="worker")

        self.assertEqual(result["status"], "SUCCEEDED")
        self.assertEqual(result["review"]["status"], "APPROVED")
        self.assertEqual(len(calls), 1)
        self.assertIn("service.py", calls[0])
        self.assertNotIn("goal.resume:goal_review", state.values)

    def test_review_snapshot_excludes_unrelated_preexisting_diff(self):
        class RegistryWithUnrelated(_Registry):
            def execute_tool(self, tool_name, args, **kwargs):
                if tool_name == "git_status":
                    return ToolResult(True, " M service.py\n M unrelated.py\n")
                if tool_name == "git_diff":
                    return ToolResult(
                        True,
                        """diff --git a/service.py b/service.py
--- a/service.py
+++ b/service.py
@@ -1 +1,2 @@
+def load_user(user_id):
+    return cache[user_id]
diff --git a/unrelated.py b/unrelated.py
--- a/unrelated.py
+++ b/unrelated.py
@@ -1 +1 @@
-old = True
+old = False
""",
                    )
                return super().execute_tool(tool_name, args, **kwargs)

        registry = RegistryWithUnrelated()
        runtime = SimpleNamespace(
            workspace_id="ws",
            registry=registry,
        )
        goal = SimpleNamespace(conversation_id="conv")
        security = SimpleNamespace()
        snapshot, complete = GoalStepExecutor._collect_review_snapshot(
            runtime, goal, security, "turn", ["service.py"]
        )
        self.assertTrue(complete)
        self.assertIn("service.py", snapshot)
        self.assertIn("return cache[user_id]", snapshot)
        self.assertNotIn("unrelated.py", snapshot)
        self.assertNotIn("old = False", snapshot)

    def test_explicit_reviewer_profile_overrides_execution_fallback(self):
        review_profile = object()
        execute_profile = object()
        calls = []
        router = SimpleNamespace(
            config=SimpleNamespace(
                routing={"adversarial-review": "review"},
                profiles={"review": review_profile},
            ),
            resolve_profile_for_task=lambda task: calls.append(task) or ("execute", execute_profile),
        )
        runtime = SimpleNamespace(processor=SimpleNamespace(router=router))
        name, profile = GoalStepExecutor._review_profile(runtime)
        self.assertEqual(name, "review")
        self.assertIs(profile, review_profile)
        self.assertEqual(calls, [])

        router.config.routing = {}
        name, profile = GoalStepExecutor._review_profile(runtime)
        self.assertEqual((name, profile), ("execute", execute_profile))
        self.assertEqual(calls, ["code-generation"])


if __name__ == "__main__":
    unittest.main()
