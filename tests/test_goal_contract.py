from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from kitt.core.turn_command import TurnCommand
from kitt.core.turn_events import ToolStarted, TurnCancelled, TurnCompleted, TurnFailed
from kitt.core.cancellation import CancellationRegistry
from kitt.core.turn_processor import TurnProcessor
from kitt.core.runtime_config import RuntimeConfig
from kitt.domain.entities import ModelProfile
from kitt.core.turn_tool_loop import (
    _goal_contract_uses_outer_verification,
    _task_plan_context,
)
from kitt.goals.contract import ContractPlanner
from kitt.goals.contract_execution import ContractStep, build_contract_prompt
from kitt.goals.contract_store import ContractLeaseError
from kitt.goals.auto_contract import (
    automatic_contract_capabilities,
    cancel_automatic_contract,
    goal_inputs_key,
    iter_automatic_contract,
)
from kitt.goals.contract_validation import ContractValidator, parse_validation_report
from kitt.goals.progress import publish_goal_progress
from kitt.goals.scheduler import GoalScheduler
from kitt.goals.service import GoalService
from kitt.history.database import HistoryDatabase
from kitt.history.migrations import CURRENT_SCHEMA_VERSION, MigrationRunner
from kitt.security.capabilities import CAP_MCP_CALL


def _done_result(paths=None):
    return {
        "status": "ITEM_DONE",
        "tokens": 0,
        "cost": 0.0,
        "contract_evidence": {
            "changed_paths": list(paths or []),
            "host_checks": {},
            "validation": {
                "verdict": "OK",
                "evidence": ["independent validation passed"],
                "issues": [],
            },
            "verification": {
                "success": True,
                "score": 1.0,
                "checks": [],
            },
        },
    }


def _item(
    local_id: str,
    *,
    kind: str = "task",
    depends_on=None,
    criteria=None,
    check_ids=None,
    paths=None,
):
    return {
        "local_id": local_id,
        "kind": kind,
        "title": f"Title {local_id}",
        "prompt": f"Implement {local_id}",
        "validation_prompt": f"Validate {local_id}",
        "success_criteria": list(criteria or [f"{local_id} works"]),
        "check_ids": list(check_ids or []),
        "paths": list(paths or []),
        "depends_on": list(depends_on or []),
    }


class GoalContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = HistoryDatabase(":memory:")
        now = 1.0
        with self.db.get_connection() as connection:
            connection.execute(
                """INSERT INTO workspaces(
                    id,canonical_path_hash,display_name,created_at,last_opened_at
                ) VALUES(?,?,?,?,?)""",
                ("ws", "hash", "workspace", now, now),
            )
            connection.execute(
                """INSERT INTO conversations(
                    id,workspace_id,title,status,created_at,updated_at
                ) VALUES(?,?,?,?,?,?)""",
                ("conv", "ws", "contract tests", "ACTIVE", now, now),
            )
        self.goals = GoalService(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def _contract(self, *, max_attempts=3):
        return self.goals.create_contract(
            "conv",
            "Complete the request",
            [
                _item("T01", paths=["kitt/a.py"]),
                _item(
                    "FINAL",
                    kind="final",
                    depends_on=["T01"],
                    paths=["kitt/a.py"],
                ),
            ],
            ["repo.read", "repo.search", "artifact.read"],
            None,
            1800,
            max_attempts,
        )

    def test_planner_rejects_model_authority_and_path_traversal(self):
        planner = ContractPlanner(
            SimpleNamespace(canonical_root=Path(self.tmp.name))
        )
        valid = {
            "items": [
                _item("T01", paths=["src/a.py"], check_ids=["python.tests"]),
                _item("FINAL", kind="final", depends_on=["T01"]),
            ]
        }
        items = planner.validate(valid)
        self.assertEqual(items[-1]["depends_on"], ["T01"])
        self.assertEqual(items[-1]["check_ids"], ["python.tests"])
        self.assertEqual(items[-1]["paths"], ["src/a.py"])
        self.assertEqual(
            items[-1]["success_criteria"],
            ["T01 works", "FINAL works"],
        )

        for forbidden in ("capabilities", "gates", "argv"):
            payload = json.loads(json.dumps(valid))
            payload["items"][0][forbidden] = ["process.run"]
            with self.assertRaises(ValueError):
                planner.validate(payload)

        traversal = json.loads(json.dumps(valid))
        traversal["items"][0]["paths"] = ["../escape.py"]
        with self.assertRaises((ValueError, PermissionError)):
            planner.validate(traversal)

        unknown_check = json.loads(json.dumps(valid))
        unknown_check["items"][0]["check_ids"] = ["model.invented.check"]
        with self.assertRaises(ValueError):
            planner.validate(unknown_check)

        path_overflow = {
            "items": [
                _item("T01", paths=[f"a/{idx}.py" for idx in range(40)]),
                _item(
                    "T02",
                    paths=[f"b/{idx}.py" for idx in range(40)],
                    depends_on=["T01"],
                ),
                _item("FINAL", kind="final", depends_on=["T02"]),
            ]
        }
        with self.assertRaises(ValueError):
            planner.validate(path_overflow)

    def test_planner_requires_ordered_dependencies_and_final_item(self):
        planner = ContractPlanner(
            SimpleNamespace(canonical_root=Path(self.tmp.name))
        )
        unknown = {
            "items": [
                _item("T01", depends_on=["missing"]),
                _item("FINAL", kind="final", depends_on=["T01"]),
            ]
        }
        with self.assertRaises(ValueError):
            planner.validate(unknown)

        no_final = {"items": [_item("T01")]}
        with self.assertRaises(ValueError):
            planner.validate(no_final)

        final_only = {"items": [_item("FINAL", kind="final")]}
        with self.assertRaisesRegex(ValueError, "at least one task plus FINAL"):
            planner.validate(final_only)

    def test_high_risk_plan_is_reviewed_before_acceptance(self):
        runtime = SimpleNamespace(
            canonical_root=Path(self.tmp.name),
            workspace_id="ws",
            processor=SimpleNamespace(),
        )
        planner = ContractPlanner(runtime)
        risky = {
            "items": [
                _item("T01", paths=["security/policy.py"]),
                _item("FINAL", kind="final", depends_on=["T01"]),
            ]
        }
        responses = (
            f"KITT_CONTRACT: {json.dumps(risky)}",
            'KITT_PLAN_REVIEW: {"verdict":"OK","issues":[]}',
        )
        with patch.object(
            planner, "_run_plan_turn", side_effect=responses
        ) as run:
            items = planner.plan("conv", "Harden the security policy")
        self.assertEqual(items[0]["local_id"], "T01")
        self.assertEqual(run.call_count, 2)
        self.assertEqual(
            run.call_args_list[1].kwargs["principal_type"],
            "CONTRACT_REVIEWER",
        )

    def test_long_high_risk_contract_reaches_review_with_8k_proxy_context(self):
        # The real prompt builder failed here after planning succeeded: the
        # global 4096-token output floor starved the required tool schema.
        objective = ("Implement authentication and isolated user journeys. " * 125).strip()
        tasks = [
            _item(f"T{index:02}", paths=["src/auth/domain.ts"])
            for index in range(1, 8)
        ]
        for item in tasks:
            item["prompt"] = "Implement the scoped domain behavior. " * 9
            item["validation_prompt"] = "Verify authorization and acceptance criteria. " * 4
            item["success_criteria"] = ["Role boundaries hold", "Domain behavior works"]
        payload = {"items": [*tasks, _item("FINAL", kind="final", depends_on=["T07"])]}
        dispatched = []
        output_limits = []
        allocations = []

        class Client:
            profile = ModelProfile(
                backend="kitt-reverse-proxy", protocol="kitt-reverse-proxy",
                model="gemini-web", base_url="http://127.0.0.1:3000",
                context_window=8192, max_output_tokens=4096, supports_tools=True,
            )

            def chat_stream(self, messages, **kwargs):
                dispatched.append(messages[-1]["content"])
                output_limits.append(kwargs["max_output_tokens"])
                if len(dispatched) == 1:
                    yield f"KITT_CONTRACT: {json.dumps(payload)}"
                else:
                    yield 'KITT_PLAN_REVIEW: {"verdict":"OK","issues":[]}'

        processor = TurnProcessor(
            root_dir=self.tmp.name, workspace_id="ws", execution_client=Client(),
            config=RuntimeConfig(history_enabled=False, persistence_enabled=False),
            event_callback=lambda name, payload: allocations.append(payload["allocated"])
            if name == "BudgetApplied" else None,
        )
        try:
            planner = ContractPlanner(SimpleNamespace(
                canonical_root=Path(self.tmp.name), workspace_id="ws", processor=processor,
            ))
            accepted = planner.plan("conv", objective)
            self.assertEqual(len(accepted), 8)
            self.assertEqual(len(dispatched), 2)
            self.assertEqual(output_limits, [4096, 2048])
            self.assertEqual(len(allocations), 2)
            for allocation in allocations:
                self.assertLessEqual(
                    allocation["total_input_tokens"] + allocation["reserved_output_tokens"], 8192,
                )
            self.assertIn(json.dumps(objective), dispatched[1])
            review = json.loads(dispatched[1].split("Contract:\n", 1)[1].split("\n\nEmit exactly", 1)[0])
            self.assertEqual(review["objective"], objective)
            self.assertEqual(review["items"], accepted)
        finally:
            processor.close()
            processor.registry.close()

    def test_oversized_prompt_fails_with_scoped_diagnostic_without_prompt_content(self):
        profile = ModelProfile(
            backend="kitt-reverse-proxy", protocol="kitt-reverse-proxy",
            model="gemini-web", base_url="http://127.0.0.1:3000",
            context_window=8192, max_output_tokens=4096, supports_tools=True,
        )
        processor = TurnProcessor(
            root_dir=self.tmp.name, workspace_id="ws",
            execution_client=SimpleNamespace(profile=profile),
            config=RuntimeConfig(history_enabled=False, persistence_enabled=False),
        )
        command = TurnCommand("conv", "PRIVATE_REQUEST " * 2000, mode="plan", no_history=True)
        try:
            with self.assertLogs("kitt.core.turn_processor", level="DEBUG") as captured:
                events = list(processor.run_turn(command))
            self.assertIsInstance(events[-1], TurnFailed)
            self.assertEqual(events[-1].turn_id, command.turn_id)
            failure = next(record for record in captured.records if record.msg == "turn.failure")
            self.assertEqual(failure.extra_data["turn_id"], command.turn_id)
            self.assertEqual(failure.extra_data["error_type"], "PromptTooLargeError")
            self.assertNotIn("PRIVATE_REQUEST", str(failure.extra_data))
        finally:
            processor.close()
            processor.registry.close()

    def test_scheduler_commits_terminal_host_block_without_global_retry(self):
        goal = self._contract()

        def executor(_goal, **_kwargs):
            return {
                "status": "BLOCKED",
                "tokens": 0,
                "cost": 0.0,
                "error": "Write capability is not granted",
                "block_reason": "POLICY",
            }

        scheduler = GoalScheduler(
            self.db,
            self.goals,
            runtime_step_executor=executor,
            poll_interval_seconds=0.01,
        )
        scheduler.schedule_goal(goal.id, heartbeat_enabled=True)
        result = scheduler.check_and_execute_due()
        self.assertEqual(result[0]["status"], "BLOCKED")
        updated = self.goals.get(goal.id)
        self.assertEqual(updated.state, "FAILED")
        self.assertEqual(updated.retries_used, 0)
        self.assertEqual(updated.failures_used, 0)

    def test_goal_contract_turn_defers_completion_to_outer_verifier_without_task_plan(self):
        class Plans:
            def inspect(self, _conversation_id, _turn_id):
                return None

        security = SimpleNamespace(principal_type="GOAL")
        self.assertTrue(
            _goal_contract_uses_outer_verification(
                Plans(), security, "conv", "turn-goal"
            )
        )
        self.assertFalse(
            _goal_contract_uses_outer_verification(
                Plans(),
                SimpleNamespace(principal_type="USER"),
                "conv",
                "turn-user",
            )
        )

        class NestedPlans:
            def inspect(self, _conversation_id, _turn_id):
                return {"tasks": []}

        self.assertFalse(
            _goal_contract_uses_outer_verification(
                NestedPlans(), security, "conv", "turn-goal"
            )
        )

    def test_goal_contract_omits_task_plan_host_context_without_nested_plan(self):
        original = {"schema_version": 1, "segments": [{"id": "base"}]}

        class Plans:
            def __init__(self, plan):
                self.plan = plan
                self.context_calls = 0

            def inspect(self, _conversation_id, _turn_id):
                return self.plan

            def context(self, envelope, _conversation_id, _turn_id):
                self.context_calls += 1
                return {**envelope, "task_plan_context": True}

        no_plan = Plans(None)
        result = _task_plan_context(
            no_plan,
            SimpleNamespace(principal_type="GOAL"),
            original,
            "conv",
            "turn-goal",
        )
        self.assertIs(result, original)
        self.assertEqual(no_plan.context_calls, 0)

        real_plan = Plans({"tasks": []})
        result = _task_plan_context(
            real_plan,
            SimpleNamespace(principal_type="GOAL"),
            original,
            "conv",
            "turn-goal",
        )
        self.assertTrue(result["task_plan_context"])
        self.assertEqual(real_plan.context_calls, 1)

        user_turn = Plans(None)
        result = _task_plan_context(
            user_turn,
            SimpleNamespace(principal_type="USER"),
            original,
            "conv",
            "turn-user",
        )
        self.assertTrue(result["task_plan_context"])
        self.assertEqual(user_turn.context_calls, 1)

    def test_contract_step_prompt_rejects_nested_task_plan_verification(self):
        item = SimpleNamespace(local_id="T01", status="RUNNING")
        runtime = SimpleNamespace(
            goals=SimpleNamespace(
                contract_items=lambda _goal_id: [
                    item,
                    SimpleNamespace(local_id="FINAL", status="PENDING"),
                ]
            )
        )
        goal = SimpleNamespace(id="goal-1")
        step = ContractStep(
            item=item,
            goal=SimpleNamespace(objective="Implement the current slice."),
            completion_key="completion",
            completion_state=None,
        )
        prompt = build_contract_prompt(runtime, goal, step, None)
        self.assertIn("do not call plan.submit/plan.verify", prompt)
        self.assertIn("post-turn checks", prompt)

    def test_validation_report_is_fail_closed(self):
        malformed = parse_validation_report("not a report")
        self.assertFalse(malformed.ok)

        no_evidence = parse_validation_report(
            'KITT_VALIDATION_REPORT: {"verdict":"OK","evidence":[],"issues":[]}'
        )
        self.assertFalse(no_evidence.ok)

        ok = parse_validation_report(
            'KITT_VALIDATION_REPORT: {"verdict":"OK","evidence":["pytest passed"],"issues":[]}'
        )
        self.assertTrue(ok.ok)

    def test_final_validation_receives_original_user_request(self):
        class Processor:
            def __init__(self):
                self.command = None

            def run_turn(self, command):
                self.command = command
                yield TurnCompleted(
                    response=(
                        'KITT_VALIDATION_REPORT: '
                        '{"verdict":"OK","evidence":["workspace inspected"],"issues":[]}'
                    )
                )

        processor = Processor()
        runtime = SimpleNamespace(workspace_id="ws", processor=processor, database=self.db)
        goal = SimpleNamespace(
            id="goal-1",
            conversation_id="conv",
            objective="Keep audit logging and migrate the API.",
        )
        item = SimpleNamespace(
            kind="final",
            local_id="FINAL",
            title="Final validation",
            validation_prompt="Validate the integrated result.",
        )

        report, _, _ = ContractValidator(runtime).validate(
            goal=goal,
            item=item,
            deterministic_evidence="checks passed",
            changed_paths=[],
            snapshot="",
        )

        self.assertTrue(report.ok)
        self.assertIn(goal.objective, processor.command.prompt)
        self.assertTrue(processor.command.no_history)

    def test_contract_creation_is_atomic_and_guards_success(self):
        goal = self._contract(max_attempts=4)
        items = self.goals.contract_items(goal.id)
        self.assertEqual(len(items), 2)
        self.assertEqual(goal.max_turns, 8)
        with self.assertRaises(ValueError):
            self.goals.finish(goal.id, True)

        legacy = self.goals.create("conv", "legacy goal")
        finished = self.goals.finish(legacy.id, True)
        self.assertEqual(finished.state, "SUCCEEDED")

    def test_scheduler_advances_only_after_done_and_finishes_all_items(self):
        goal = self._contract()
        calls = []

        def executor(current_goal, **_kwargs):
            current = self.goals.current_item(current_goal.id)
            calls.append(current.local_id)
            result = _done_result(["kitt/a.py"])
            result["tokens"] = 1
            return result

        scheduler = GoalScheduler(
            self.db,
            self.goals,
            runtime_step_executor=executor,
            poll_interval_seconds=0.01,
        )
        scheduler.schedule_goal(goal.id, heartbeat_enabled=True)

        first = scheduler.check_and_execute_due()
        self.assertEqual(first[0]["status"], "ITEM_DONE")
        after_first = self.goals.get(goal.id)
        self.assertEqual(after_first.state, "ACTIVE")
        self.assertEqual(
            [item.status for item in self.goals.contract_items(goal.id)],
            ["DONE", "PENDING"],
        )

        second = scheduler.check_and_execute_due()
        self.assertEqual(second[0]["status"], "ITEM_DONE")
        self.assertEqual(self.goals.get(goal.id).state, "SUCCEEDED")
        self.assertEqual(calls, ["T01", "FINAL"])
        self.assertTrue(self.goals.contract_complete(goal.id))

    def test_retry_exhaustion_blocks_item_and_resume_preserves_done_items(self):
        goal = self._contract(max_attempts=1)

        def executor(current_goal, **_kwargs):
            current = self.goals.current_item(current_goal.id)
            if current.local_id == "T01":
                return _done_result()
            return {
                "status": "INCOMPLETE",
                "tokens": 0,
                "cost": 0.0,
                "error": "final validation failed",
            }

        scheduler = GoalScheduler(
            self.db,
            self.goals,
            runtime_step_executor=executor,
            poll_interval_seconds=0.01,
        )
        scheduler.schedule_goal(goal.id, heartbeat_enabled=True)
        scheduler.check_and_execute_due()
        result = scheduler.check_and_execute_due()
        self.assertEqual(result[0]["status"], "ITEM_EXHAUSTED")
        self.assertEqual(self.goals.get(goal.id).state, "FAILED")
        before_resume = self.goals.contract_items(goal.id)
        self.assertEqual(before_resume[0].status, "DONE")
        self.assertEqual(before_resume[1].status, "BLOCKED")

        resumed_item = self.goals.resume_contract(goal.id, conversation_id="conv")
        self.assertEqual(resumed_item.local_id, "FINAL")
        after_resume = self.goals.contract_items(goal.id)
        self.assertEqual(after_resume[0].status, "DONE")
        self.assertEqual(after_resume[1].status, "PENDING")
        self.assertEqual(after_resume[1].attempts, 0)
        self.assertEqual(self.goals.get(goal.id).state, "ACTIVE")

    def test_scheduler_without_executor_does_not_consume_contract_attempt(self):
        goal = self._contract()
        scheduler = GoalScheduler(
            self.db,
            self.goals,
            runtime_step_executor=None,
            poll_interval_seconds=0.01,
        )
        scheduler.schedule_goal(goal.id, heartbeat_enabled=True)
        result = scheduler.check_and_execute_due()
        self.assertEqual(result[0]["status"], "DUE_NO_EXECUTOR")
        self.assertEqual(self.goals.contract_items(goal.id)[0].attempts, 0)

    def test_contract_outcome_requires_current_lease(self):
        goal = self._contract()
        scheduler = GoalScheduler(self.db, self.goals, runtime_step_executor=lambda *_a, **_k: {})
        lease_id = scheduler._claim(goal.id)
        self.assertIsNotNone(lease_id)
        item = self.goals.begin_contract_attempt(
            goal.id,
            lease_id=lease_id,
            lease_owner_id=scheduler.worker_id,
        )
        with self.assertRaises(ContractLeaseError):
            self.goals.commit_contract_outcome(
                goal.id,
                item.id,
                lease_id="stale",
                lease_owner_id=scheduler.worker_id,
                outcome="DONE",
            )

    def test_resume_refuses_active_or_leased_contract(self):
        goal = self._contract()
        with self.assertRaises(ValueError):
            self.goals.resume_contract(goal.id, conversation_id="conv")

        scheduler = GoalScheduler(
            self.db,
            self.goals,
            runtime_step_executor=lambda *_a, **_k: {},
        )
        lease_id = scheduler._claim(goal.id)
        self.assertIsNotNone(lease_id)
        with self.assertRaises((ValueError, ContractLeaseError)):
            self.goals.resume_contract(goal.id, conversation_id="conv")

    def test_cancel_during_planning_and_before_scheduling_starts_no_work(self):
        from kitt.runtime.state import RuntimeStateStore
        from unittest.mock import Mock
        for cancel_phase in ("planning", "creation"):
            with self.subTest(phase=cancel_phase):
                registry = CancellationRegistry()
                scheduler = SimpleNamespace(_running=True, schedule_goal=Mock(return_value=True))
                processor = SimpleNamespace(cancellation_registry=registry)
                runtime = SimpleNamespace(canonical_root=Path(self.tmp.name), database=self.db,
                    workspace_id="ws", goals=self.goals, goal_scheduler=scheduler, processor=processor)
                command = TurnCommand(conversation_id="conv", prompt="implement", turn_id=f"cancel-{cancel_phase}")
                planned = [_item("T01"), _item("FINAL", kind="final")]
                def plan(*args, **kwargs):
                    if cancel_phase == "planning":
                        self.assertTrue(cancel_automatic_contract(runtime, "conv", command.turn_id, "cancel"))
                    return planned
                create = self.goals.create_contract
                def create_then_cancel(*args, **kwargs):
                    goal = create(*args, **kwargs)
                    registry.cancel(command.turn_id)
                    return goal
                with patch("kitt.goals.auto_contract.ContractPlanner.plan", side_effect=plan), patch.object(
                    self.goals, "create_contract", side_effect=create_then_cancel if cancel_phase == "creation" else create,
                ):
                    events = list(iter_automatic_contract(runtime, command))
                self.assertIsInstance(events[-1], TurnCancelled)
                scheduler.schedule_goal.assert_not_called()
                self.assertIsNone(RuntimeStateStore(self.db, "ws", "conv").get(f"auto-contract:{command.turn_id}"))
                if cancel_phase == "creation":
                    self.assertEqual(self.goals.latest_contract("conv").state, "CANCELLED")

    def test_planner_preserves_selected_inputs_and_registers_inner_cancellation(self):
        from kitt.runtime.state import RuntimeStateStore
        registry = CancellationRegistry()
        captured = []
        cancelled = []
        source = TurnCommand(conversation_id="conv", prompt="implement", explicit_files={"selected.py", "reference.png"})
        def run(command):
            captured.append(command)
            self.assertTrue(cancel_automatic_contract(runtime, "conv", source.turn_id, "cancel"))
            yield TurnCompleted(response="should not be accepted")
        def cancel(turn_id, *args, **kwargs):
            cancelled.append(turn_id)
            yield TurnCancelled(reason="cancel")
        runtime = SimpleNamespace(canonical_root=Path(self.tmp.name), database=self.db, workspace_id="ws",
            processor=SimpleNamespace(run_turn=run, cancel_turn=cancel, cancellation_registry=registry))
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            ContractPlanner(runtime).plan("conv", "implement", source_command=source)
        self.assertEqual(captured[0].explicit_files, {"selected.py"})
        self.assertEqual(captured[0].attachments, {"reference.png"})
        self.assertEqual(cancelled, [captured[0].turn_id])
        self.assertTrue(registry.is_cancelled(source.turn_id))

    def test_cancelled_goal_cannot_be_rescheduled(self):
        goal = self._contract()
        self.goals.cancel_contract(goal.id, "cancel", conversation_id="conv")
        scheduler = GoalScheduler(self.db, self.goals, lambda goal: {})
        self.assertFalse(scheduler.schedule_goal(goal.id))
        self.assertEqual(self.goals.get(goal.id).state, "CANCELLED")

    def test_automatic_contract_wraps_normal_auto_turn(self):
        created_goal = SimpleNamespace(
            id="goal-auto",
            conversation_id="conv",
            state="ACTIVE",
            max_wall_seconds=60,
            last_error=None,
        )
        completed_goal = SimpleNamespace(
            id="goal-auto",
            conversation_id="conv",
            state="SUCCEEDED",
            max_wall_seconds=60,
            last_error=None,
        )
        contract_items = [
            SimpleNamespace(
                local_id="T01",
                status="DONE",
                attempts=1,
                max_attempts=5,
                title="Implement",
                last_feedback=None,
            ),
            SimpleNamespace(
                local_id="FINAL",
                status="DONE",
                attempts=1,
                max_attempts=5,
                title="Validate",
                last_feedback=None,
            ),
        ]

        class Goals:
            def __init__(self):
                self.created = None

            def create_contract(self, *args, **kwargs):
                self.created = (args, kwargs)
                return created_goal

            def get(self, _goal_id):
                return completed_goal

            def contract_items(self, _goal_id):
                return contract_items

        test_case = self
        class Scheduler:
            def __init__(self):
                self.scheduled = None
                self._running = True

            def schedule_goal(self, goal_id, **kwargs):
                self.scheduled = (goal_id, kwargs)
                from kitt.runtime.state import RuntimeStateStore
                inputs = RuntimeStateStore(test_case.db, "ws", "conv").get(goal_inputs_key(goal_id))
                test_case.assertEqual(inputs, {"explicit_files": ["selected.py"], "attachments": ["reference.png"]})
                publish_goal_progress(
                    goal_id,
                    ToolStarted(
                        tool_name="repo.read",
                        args={"path": "package.json"},
                        call_id="call-progress",
                    ),
                )
                return True

        runtime = SimpleNamespace(
            canonical_root=Path(self.tmp.name),
            database=self.db,
            workspace_id="ws",
            goals=Goals(),
            goal_scheduler=Scheduler(),
        )
        command = TurnCommand(
            conversation_id="conv",
            prompt="implement and validate the request",
            mode="auto",
            turn_id="turn-auto",
            explicit_files={"selected.py", "reference.png"},
        )
        planned = [
            _item("T01"),
            _item("FINAL", kind="final", depends_on=["T01"]),
        ]

        with patch(
            "kitt.goals.auto_contract.ContractPlanner.plan",
            return_value=planned,
        ), patch(
            "kitt.goals.auto_contract.render_contract",
            return_value="contract complete",
        ):
            events = list(iter_automatic_contract(runtime, command))

        self.assertEqual(type(events[0]).__name__, "TurnStarted")
        self.assertEqual(type(events[-1]).__name__, "TurnCompleted")
        self.assertEqual(events[-1].response, "contract complete")
        live_tools = [event for event in events if isinstance(event, ToolStarted)]
        self.assertEqual(len(live_tools), 1)
        self.assertEqual(live_tools[0].tool_name, "repo.read")
        self.assertEqual(runtime.goal_scheduler.scheduled[0], "goal-auto")
        self.assertIn(CAP_MCP_CALL, automatic_contract_capabilities())
        capabilities = runtime.goals.created[0][3]
        self.assertIn(CAP_MCP_CALL, capabilities)

    def test_contract_approval_resume_keeps_attempt_count(self):
        goal = self._contract(max_attempts=4)
        with self.db.get_connection() as connection:
            connection.execute(
                "UPDATE goals SET state='WAITING_APPROVAL',lease_id=NULL,"
                "lease_owner_id=NULL,lease_expires_at=NULL WHERE id=?",
                (goal.id,),
            )
            item = self.goals.current_item(goal.id)
            connection.execute(
                "UPDATE goal_contract_items SET status='PENDING',attempts=2 WHERE id=?",
                (item.id,),
            )

        resumed = self.goals.resume_after_approval(
            goal.id,
            conversation_id="conv",
        )

        self.assertIsNotNone(resumed)
        self.assertEqual(resumed.state, "ACTIVE")
        current = self.goals.current_item(goal.id)
        self.assertEqual(current.status, "PENDING")
        self.assertEqual(current.attempts, 2)

    def test_denied_contract_approval_blocks_current_item(self):
        goal = self._contract()
        with self.db.get_connection() as connection:
            connection.execute(
                "UPDATE goals SET state='WAITING_APPROVAL',lease_id=NULL,"
                "lease_owner_id=NULL,lease_expires_at=NULL WHERE id=?",
                (goal.id,),
            )

        blocked = self.goals.block_waiting_contract(
            goal.id,
            "denied",
            conversation_id="conv",
        )

        self.assertIsNotNone(blocked)
        self.assertEqual(blocked.status, "BLOCKED")
        self.assertEqual(self.goals.get(goal.id).state, "FAILED")

    def test_no_history_turns_get_isolated_provider_session_keys(self):
        processor = object.__new__(TurnProcessor)
        processor._proxy_session_key = "agent-window:test"
        first = processor._provider_session_key(
            None,
            "conv",
            isolated_turn_id="turn-a",
        )
        second = processor._provider_session_key(
            None,
            "conv",
            isolated_turn_id="turn-b",
        )
        self.assertNotEqual(first, second)
        self.assertIn(":isolated:conv:turn-a", first)

    def test_fresh_schema_and_v11_upgrade_create_contract_table(self):
        self.assertEqual(CURRENT_SCHEMA_VERSION, 12)
        with self.db.get_connection() as connection:
            table = connection.execute(
                """SELECT name FROM sqlite_master
                   WHERE type='table' AND name='goal_contract_items'"""
            ).fetchone()
            self.assertIsNotNone(table)

        connection = sqlite3.connect(":memory:")
        try:
            connection.execute("CREATE TABLE schema_info(version INTEGER PRIMARY KEY)")
            connection.execute("INSERT INTO schema_info(version) VALUES(11)")
            MigrationRunner().migrate(connection)
            version = connection.execute(
                "SELECT version FROM schema_info LIMIT 1"
            ).fetchone()[0]
            table = connection.execute(
                """SELECT name FROM sqlite_master
                   WHERE type='table' AND name='goal_contract_items'"""
            ).fetchone()
            self.assertEqual(version, 12)
            self.assertIsNotNone(table)
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
