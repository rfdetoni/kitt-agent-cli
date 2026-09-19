from __future__ import annotations

import unittest

from kitt.core.autonomy_policy import AutonomyPolicy
from kitt.security.action_review import ActionReviewBroker
from kitt.tools.policy_engine import PolicyEngine


class ActionReviewBrokerTests(unittest.TestCase):
    def test_balanced_auto_reviews_read_only_command_only_with_strong_sandbox(self):
        engine = PolicyEngine(autonomy=AutonomyPolicy.preset("balanced"))
        args = {"argv": ["git", "status"], "network": False}

        allowed = engine.review_ask_action(
            "run_command",
            args,
            permission="ASK",
            origin="MODEL",
            sandbox_strong=True,
        )
        self.assertEqual(allowed.decision, "SANDBOX_ALLOW")
        self.assertTrue(allowed.allowed)
        self.assertEqual(allowed.command_classification, "ALLOW")

        weak = engine.review_ask_action(
            "run_command",
            args,
            permission="ASK",
            origin="MODEL",
            sandbox_strong=False,
        )
        self.assertEqual(weak.decision, "ASK_USER")
        self.assertFalse(weak.allowed)

    def test_network_and_control_plane_elevations_never_auto_review(self):
        broker = ActionReviewBroker()
        for network, control in ((True, False), (False, True)):
            result = broker.review(
                tool_name="run_command",
                permission="ASK",
                origin="MODEL",
                auto_review_enabled=True,
                command_classification="ALLOW",
                sandbox_strong=True,
                network_requested=network,
                control_plane_elevation=control,
            )
            self.assertEqual(result.decision, "ASK_USER")
            self.assertFalse(result.allowed)

    def test_supervised_mode_preserves_explicit_approval_semantics(self):
        engine = PolicyEngine(autonomy=AutonomyPolicy.preset("supervised"))
        review = engine.review_ask_action(
            "run_command",
            {"argv": ["git", "status"]},
            permission="ASK",
            origin="MODEL",
            sandbox_strong=True,
        )
        self.assertEqual(review.decision, "ASK_USER")

    def test_reviewer_never_overrides_hard_deny(self):
        broker = ActionReviewBroker()
        result = broker.review(
            tool_name="run_command",
            permission="DENY",
            origin="MODEL",
            auto_review_enabled=True,
            command_classification="ALLOW",
            sandbox_strong=True,
        )
        self.assertEqual(result.decision, "DENY")
        self.assertFalse(result.allowed)

    def test_non_read_only_command_stays_ask_even_with_sandbox(self):
        engine = PolicyEngine(autonomy=AutonomyPolicy.preset("balanced"))
        review = engine.review_ask_action(
            "run_command",
            {"argv": ["pytest"]},
            permission="ASK",
            origin="MODEL",
            sandbox_strong=True,
        )
        self.assertEqual(review.decision, "ASK_USER")
        self.assertEqual(review.command_classification, "ASK")


if __name__ == "__main__":
    unittest.main()
