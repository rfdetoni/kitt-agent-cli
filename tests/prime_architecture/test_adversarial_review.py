import json
import unittest
from types import SimpleNamespace

from kitt.goals.completion import AutonomousCompletionEngine, CompletionCheck, CompletionVerification
from kitt.goals.review import ADVERSARIAL_REVIEW_PREFIX, AdversarialCodeReviewer


ALL_AREAS = [
    "correctness", "security", "concurrency", "performance",
    "resource_management", "data_integrity", "architecture",
    "maintainability", "tests", "compatibility",
]
CORE_AREAS = ["correctness", "security", "maintainability", "tests"]


SNAPSHOT = """[GIT DIFF]
diff --git a/service.py b/service.py
--- a/service.py
+++ b/service.py
@@ -1,2 +1,4 @@
+def load_user(user_id):
+    return cache[user_id]
"""


def _verification():
    return CompletionVerification(
        success=True,
        score=1.0,
        checks=[CompletionCheck("gate", "Tests", True, "pytest passed")],
    )


def _response(payload):
    return f"{ADVERSARIAL_REVIEW_PREFIX} {json.dumps(payload)}"


class TestAdversarialCodeReviewer(unittest.TestCase):
    def test_prompt_uses_independent_senior_reviewer_posture(self):
        seen = {}

        def call(system, user):
            seen["system"] = system
            seen["user"] = user
            return _response(
                {
                    "status": "APPROVED",
                    "summary": "No required defect remains.",
                    "reviewed_areas": ALL_AREAS,
                    "findings": [],
                    "approval_evidence": "Checked cache access, error paths, and test evidence in the supplied diff.",
                }
            )

        reviewer = AdversarialCodeReviewer(call)
        review = reviewer.review(
            objective="Implement cache lookup",
            success_criteria=["Tests pass"],
            verification=_verification(),
            change_snapshot=SNAPSHOT,
        )
        self.assertTrue(review.approved)
        self.assertIn("competent but inexperienced", seen["system"])
        self.assertIn("do not trust the implementer's completion claim", seen["user"].lower())
        self.assertNotIn("chain-of-thought", seen["user"].lower())

    def test_required_finding_must_be_anchored_in_snapshot(self):
        reviewer = AdversarialCodeReviewer(
            lambda *_: _response(
                {
                    "status": "CHANGES_REQUIRED",
                    "summary": "Potential SQL bug",
                    "reviewed_areas": CORE_AREAS,
                    "findings": [
                        {
                            "severity": "HIGH",
                            "confidence": 0.95,
                            "category": "correctness",
                            "title": "Imaginary SQL issue",
                            "location": "repository.py:10",
                            "problem": "Uses interpolated SQL",
                            "impact": "SQL injection",
                            "required_change": "Parameterize query",
                            "evidence": "cursor.execute(f'SELECT * FROM users WHERE id={user_id}')",
                            "required": True,
                        }
                    ],
                    "approval_evidence": "",
                }
            )
        )
        review = reviewer.review(
            objective="Implement cache lookup",
            success_criteria=[],
            verification=_verification(),
            change_snapshot=SNAPSHOT,
        )
        self.assertFalse(review.approved)
        self.assertEqual(review.status, "INVALID_REVIEW")
        self.assertEqual(review.invalid_required_findings, 1)
        self.assertIn("not anchored", review.feedback)

    def test_concrete_junior_style_review_blocks_completion_and_supplies_fix(self):
        reviewer = AdversarialCodeReviewer(
            lambda *_: _response(
                {
                    "status": "CHANGES_REQUIRED",
                    "summary": "Functional happy path but missing cache-miss handling.",
                    "reviewed_areas": CORE_AREAS,
                    "findings": [
                        {
                            "severity": "HIGH",
                            "confidence": 0.95,
                            "category": "correctness",
                            "title": "Cache miss raises KeyError",
                            "location": "service.py:load_user",
                            "problem": "Direct indexing assumes every user is cached.",
                            "impact": "Valid uncached users fail instead of loading from the source.",
                            "required_change": "Handle cache miss and populate the cache after source lookup.",
                            "evidence": "return cache[user_id]",
                            "required": True,
                        }
                    ],
                    "approval_evidence": "",
                }
            )
        )
        review = reviewer.review(
            objective="Implement cache lookup",
            success_criteria=["Tests pass"],
            verification=_verification(),
            change_snapshot=SNAPSHOT,
        )
        self.assertFalse(review.approved)
        self.assertEqual(review.status, "CHANGES_REQUIRED")
        self.assertIn("Cache miss raises KeyError", review.feedback)

        merged = AutonomousCompletionEngine().include_adversarial_review(
            _verification(), review
        )
        self.assertFalse(merged.success)
        self.assertEqual(merged.checks[-1].kind, "review")
        self.assertIn("Cache miss raises KeyError", merged.feedback)
        self.assertTrue(merged.failure_signature)

    def test_approved_review_becomes_passing_authoritative_check(self):
        reviewer = AdversarialCodeReviewer(
            lambda *_: _response(
                {
                    "status": "APPROVED",
                    "summary": "Implementation is production-acceptable.",
                    "reviewed_areas": ALL_AREAS,
                    "findings": [
                        {
                            "severity": "LOW",
                            "category": "maintainability",
                            "title": "Optional naming cleanup",
                            "location": "service.py:load_user",
                            "problem": "Name could be more explicit.",
                            "impact": "Minor readability only.",
                            "required_change": "Optional rename.",
                            "evidence": "def load_user(user_id):",
                            "required": False,
                        }
                    ],
                    "approval_evidence": "Checked cache semantics, exceptions, concurrency risks, and regression-test evidence; no production-impacting defect is visible in the supplied changes.",
                }
            )
        )
        review = reviewer.review(
            objective="Implement cache lookup",
            success_criteria=[],
            verification=_verification(),
            change_snapshot=SNAPSHOT,
        )
        merged = AutonomousCompletionEngine().include_adversarial_review(
            _verification(), review
        )
        self.assertTrue(review.approved)
        self.assertTrue(merged.success)
        self.assertEqual(merged.score, 1.0)
        self.assertTrue(merged.checks[-1].passed)

    def test_incomplete_snapshot_fails_closed_without_model_call(self):
        calls = []
        reviewer = AdversarialCodeReviewer(lambda *_: calls.append(1) or "")
        review = reviewer.review(
            objective="Implement cache lookup",
            success_criteria=[],
            verification=_verification(),
            change_snapshot=SNAPSHOT,
            snapshot_complete=False,
        )
        self.assertFalse(review.approved)
        self.assertEqual(review.status, "REVIEW_INCOMPLETE")
        self.assertEqual(calls, [])

    def test_no_change_snapshot_is_not_applicable(self):
        reviewer = AdversarialCodeReviewer(lambda *_: self.fail("review model should not run"))
        review = reviewer.review(
            objective="Explain architecture",
            success_criteria=[],
            verification=_verification(),
            change_snapshot="",
        )
        self.assertTrue(review.approved)
        self.assertFalse(review.applicable)
        self.assertEqual(review.status, "NOT_APPLICABLE")

    def test_changes_required_without_valid_required_finding_fails_closed(self):
        reviewer = AdversarialCodeReviewer(
            lambda *_: _response(
                {
                    "status": "CHANGES_REQUIRED",
                    "summary": "Would refactor naming",
                    "reviewed_areas": CORE_AREAS,
                    "findings": [
                        {
                            "severity": "LOW",
                            "category": "maintainability",
                            "title": "Naming",
                            "location": "service.py",
                            "problem": "Could be prettier",
                            "impact": "Style only",
                            "required_change": "Rename",
                            "evidence": "def load_user(user_id):",
                            "required": False,
                        }
                    ],
                    "approval_evidence": "",
                }
            )
        )
        review = reviewer.review(
            objective="Implement cache lookup",
            success_criteria=[],
            verification=_verification(),
            change_snapshot=SNAPSHOT,
        )
        self.assertFalse(review.approved)
        self.assertEqual(review.status, "INVALID_REVIEW")
        self.assertIn("no valid required finding", review.feedback)

    def test_model_cannot_hide_high_severity_finding_with_required_false(self):
        reviewer = AdversarialCodeReviewer(
            lambda *_: _response(
                {
                    "status": "CHANGES_REQUIRED",
                    "summary": "Concrete correctness defect.",
                    "reviewed_areas": CORE_AREAS,
                    "findings": [
                        {
                            "severity": "HIGH",
                            "confidence": 0.98,
                            "category": "correctness",
                            "title": "Cache miss raises KeyError",
                            "location": "service.py:load_user",
                            "problem": "Direct indexing assumes every key exists.",
                            "impact": "Valid cache misses crash.",
                            "required_change": "Handle the miss explicitly.",
                            "evidence": "return cache[user_id]",
                            "required": False,
                        }
                    ],
                    "approval_evidence": "",
                }
            )
        )
        review = reviewer.review(
            objective="Implement cache lookup",
            success_criteria=[],
            verification=_verification(),
            change_snapshot=SNAPSHOT,
        )
        self.assertEqual(review.status, "CHANGES_REQUIRED")
        self.assertTrue(review.findings[0].required)

    def test_approval_requires_full_production_review_coverage(self):
        reviewer = AdversarialCodeReviewer(
            lambda *_: _response(
                {
                    "status": "APPROVED",
                    "summary": "Looks fine.",
                    "reviewed_areas": CORE_AREAS,
                    "findings": [],
                    "approval_evidence": "Checked core areas only.",
                }
            )
        )
        review = reviewer.review(
            objective="Implement cache lookup",
            success_criteria=[],
            verification=_verification(),
            change_snapshot=SNAPSHOT,
        )
        self.assertFalse(review.approved)
        self.assertEqual(review.status, "INVALID_REVIEW")
        self.assertIn("without covering all production review areas", review.feedback)


if __name__ == "__main__":
    unittest.main()
