from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from kitt.evolution.constraints import SkillConstraintValidator, split_skill
from kitt.evolution.models import EvolutionStatus
from kitt.evolution.optimizer import LLMCallBudget
from kitt.evolution.service import SkillEvolutionService
from kitt.evolution.store import EvolutionStore


BASELINE = """---
name: debugger
description: Debug failures systematically
version: 1.0.0
author: Test
---

# Debug Procedure

Inspect failure evidence before changing code. Reproduce the issue, isolate the root
cause, make the smallest safe change, run focused validation, and report evidence.
Avoid unrelated refactors and never bypass security or approval boundaries.
"""

EVOLVED_BODY = """# Debug Procedure

Reproduce first. Inspect implicated files and state one falsifiable root-cause
hypothesis. Make the smallest safe change and run focused validation. On failure,
revise the hypothesis from new evidence. Never bypass security, approvals, path,
network, or secret boundaries.
"""


class FakeSkillManager:
    def __init__(self, root: Path):
        skill_dir = root / "managed" / "debugger"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(BASELINE, encoding="utf-8")
        self.skill = SimpleNamespace(
            name="debugger",
            description="Debug failures systematically",
            version="1.0.0",
            author="Test",
            path=skill_dir,
            skill_md_content=BASELINE,
            source="managed",
            trusted=True,
        )

    @staticmethod
    def _validate_name(value):
        if value != "debugger":
            raise ValueError(value)
        return value

    def list_skills(self):
        content = (self.skill.path / "SKILL.md").read_text(encoding="utf-8")
        return [SimpleNamespace(**{**self.skill.__dict__, "skill_md_content": content})]


class FakeHistoryDB:
    def get_connection(self):
        raise AssertionError("synthetic evolution should not read history")


class FakeClient:
    def __init__(self, model: str):
        self.profile = SimpleNamespace(model=model)
        self.calls = 0
        self.closed = False

    def close(self):
        self.closed = True

    def chat(self, messages, system_prompt=None, response_format=None, **kwargs):
        self.calls += 1
        prompt = messages[-1]["content"]

        if "Generate " in prompt and "evaluation cases" in prompt:
            return json.dumps(
                [
                    {
                        "task": f"Debug failing test case {i}",
                        "rubric": (
                            "Identify root cause, make minimal safe fix, "
                            "and validate evidence."
                        ),
                    }
                    for i in range(10)
                ]
            )

        if "Review an evolved KITT skill" in prompt:
            return json.dumps(
                {
                    "approved": True,
                    "semantic_drift": False,
                    "unsafe_capability": False,
                    "quality_regression": False,
                    "reason": "More explicit procedure with unchanged scope.",
                }
            )

        if "Improve this KITT agent skill body" in prompt:
            return json.dumps({"body": EVOLVED_BODY})

        if "Evaluate the candidate output against the rubric" in prompt:
            good = "GOOD_RESULT" in prompt
            return json.dumps(
                {
                    "correctness": 0.96 if good else 0.45,
                    "procedure_following": 0.96 if good else 0.50,
                    "conciseness": 0.92 if good else 0.55,
                    "feedback": (
                        "Good evidence-driven procedure."
                        if good
                        else "Needs stronger evidence and validation."
                    ),
                }
            )

        if system_prompt and "falsifiable" in system_prompt:
            return "GOOD_RESULT: root cause isolated, minimal fix validated."
        return "BASELINE_RESULT: limited validation and repeated inspection."


class ConstraintTests(unittest.TestCase):
    def test_frontmatter_is_immutable(self):
        validator = SkillConstraintValidator()
        changed = BASELINE.replace(
            "description: Debug failures systematically",
            "description: Changed",
        )
        self.assertFalse(all(item["passed"] for item in validator.validate(BASELINE, changed)))
        self.assertIn("Debug failures systematically", split_skill(BASELINE).frontmatter)

    def test_call_budget_is_hard(self):
        budget = LLMCallBudget(6)
        for _ in range(6):
            budget.consume("test")
        with self.assertRaises(RuntimeError):
            budget.consume("overflow")


class EvolutionIntegrationTests(unittest.TestCase):
    def _service(self, root: Path):
        skills = FakeSkillManager(root)
        store = EvolutionStore(root, state_dir=root / "evolution-state")
        mutation = FakeClient("mutation")
        trial = FakeClient("trial")
        judge = FakeClient("judge")
        service = SkillEvolutionService(
            root_dir=root,
            skill_manager=skills,
            history_db=FakeHistoryDB(),
            mutation_client=mutation,
            trial_client=trial,
            judge_client=judge,
            store=store,
        )
        return service, skills, store, (mutation, trial, judge)

    def test_synthetic_evolution_stages_and_promotes_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service, skills, _, clients = self._service(root)
            try:
                run = service.evolve_skill(
                    "debugger",
                    source="synthetic",
                    generations=1,
                    population=2,
                    max_llm_calls=48,
                    synthetic_cases=10,
                )
                self.assertEqual(run.status, EvolutionStatus.STAGED)
                self.assertTrue(run.metrics["accepted"])
                self.assertTrue(run.metrics["adversarial_review"]["approved"])
                self.assertLessEqual(run.metrics["llm_calls_used"], 48)

                path = skills.skill.path / "SKILL.md"
                self.assertEqual(path.read_text(encoding="utf-8"), BASELINE)

                promoted = service.promote(run.id)
                self.assertEqual(promoted.status, EvolutionStatus.PROMOTED)
                after = path.read_text(encoding="utf-8")
                self.assertIn("falsifiable", after)
                backup = Path(promoted.metrics["backup_path"])
                self.assertTrue(backup.is_file())
                self.assertEqual(backup.read_text(encoding="utf-8"), BASELINE)
            finally:
                service.close()
            self.assertTrue(all(client.closed for client in clients))

    def test_stale_baseline_blocks_promotion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service, skills, _, _ = self._service(root)
            try:
                run = service.evolve_skill(
                    "debugger",
                    source="synthetic",
                    generations=1,
                    population=2,
                    max_llm_calls=48,
                )
                self.assertEqual(run.status, EvolutionStatus.STAGED)
                path = skills.skill.path / "SKILL.md"
                path.write_text(BASELINE + "\n# external edit\n", encoding="utf-8")
                with self.assertRaises(RuntimeError):
                    service.promote(run.id)
            finally:
                service.close()

    def test_failed_lineage_transaction_restores_skill_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service, skills, store, _ = self._service(root)
            try:
                run = service.evolve_skill(
                    "debugger",
                    source="synthetic",
                    generations=1,
                    population=2,
                    max_llm_calls=48,
                )
                original_finalize = store.finalize_promotion

                def fail_finalize(**kwargs):
                    raise RuntimeError("simulated sqlite failure")

                store.finalize_promotion = fail_finalize
                try:
                    with self.assertRaises(RuntimeError):
                        service.promote(run.id)
                finally:
                    store.finalize_promotion = original_finalize

                self.assertEqual(
                    (skills.skill.path / "SKILL.md").read_text(encoding="utf-8"),
                    BASELINE,
                )
                self.assertEqual(store.get_run(run.id).status, EvolutionStatus.STAGED)
            finally:
                service.close()


if __name__ == "__main__":
    unittest.main()
