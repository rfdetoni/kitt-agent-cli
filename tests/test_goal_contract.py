from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from kitt.core.turn_processor import TurnProcessor
from kitt.goals.contract import ContractPlanner
from kitt.goals.contract_store import ContractLeaseError
from kitt.goals.contract_validation import parse_validation_report
from kitt.goals.scheduler import GoalScheduler
from kitt.goals.service import GoalService
from kitt.history.database import HistoryDatabase
from kitt.history.migrations import CURRENT_SCHEMA_VERSION, MigrationRunner


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
            return {
                "status": "ITEM_DONE",
                "tokens": 1,
                "cost": 0.0,
                "contract_evidence": {"changed_paths": ["kitt/a.py"]},
            }

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
                return {
                    "status": "ITEM_DONE",
                    "tokens": 0,
                    "cost": 0.0,
                    "contract_evidence": {},
                }
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
