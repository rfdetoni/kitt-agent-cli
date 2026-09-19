from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kitt.core.autonomy_policy import AutonomyPolicy
from kitt.runtime.safe_runtime import SafeRuntime
from kitt.security.capabilities import CAP_REPO_WRITE
from kitt.security.risk_budget import RiskBudgetLedger
from kitt.tools.policy_engine import PolicyEngine
from kitt.tools.registry import ToolRegistry


class RiskBudgetTests(unittest.TestCase):
    def test_presets_expose_weighted_risk_limits(self):
        self.assertEqual(AutonomyPolicy.preset("read_only").max_auto_risk_per_turn, 0)
        self.assertLess(
            AutonomyPolicy.preset("balanced").max_auto_risk_per_turn,
            AutonomyPolicy.preset("autonomous").max_auto_risk_per_turn,
        )

    def test_ledger_enforces_weighted_risk_per_turn(self):
        ledger = RiskBudgetLedger()
        first = ledger.reserve(
            turn_id="turn-a", conversation_id="conv", risk_cost=2,
            max_actions=2, max_risk=3,
        )
        self.assertTrue(first.allowed)
        second = ledger.reserve(
            turn_id="turn-a", conversation_id="conv", risk_cost=2,
            max_actions=2, max_risk=3,
        )
        self.assertFalse(second.allowed)
        self.assertEqual(second.reason, "risk-limit")
        other = ledger.reserve(
            turn_id="turn-b", conversation_id="conv", risk_cost=2,
            max_actions=2, max_risk=3,
        )
        self.assertTrue(other.allowed)

    def test_explicit_origin_does_not_consume_automatic_budget(self):
        engine = PolicyEngine(
            autonomy=AutonomyPolicy.from_dict({
                "level": "autonomous",
                "max_auto_actions_per_turn": 1,
                "max_auto_risk_per_turn": 1,
            })
        )
        explicit = engine.reserve_automatic_action(
            "run_command", turn_id="turn", conversation_id="conv", origin="UI"
        )
        self.assertTrue(explicit.allowed)
        self.assertFalse(explicit.reserved)
        automatic = engine.reserve_automatic_action(
            "write_file", turn_id="turn", conversation_id="conv", origin="MODEL"
        )
        self.assertTrue(automatic.allowed)
        self.assertTrue(automatic.reserved)

    def test_direct_registry_escalates_exhausted_budget_to_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = ToolRegistry(root_dir=tmp)
            try:
                registry.policy.autonomy = AutonomyPolicy.from_dict({
                    "level": "balanced",
                    "max_auto_actions_per_turn": 1,
                    "max_auto_risk_per_turn": 1,
                })
                first = registry.execute_tool(
                    "create_directory", {"path": "one"}, turn_id="turn",
                    conversation_id="conv", workspace_id="ws",
                )
                self.assertTrue(first.success, first.error)
                self.assertEqual(first.metadata["risk_budget"]["risk_used"], 1)
                second = registry.execute_tool(
                    "create_directory", {"path": "two"}, turn_id="turn",
                    conversation_id="conv", workspace_id="ws",
                )
                self.assertFalse(second.success)
                self.assertTrue(second.requires_approval)
                self.assertFalse((Path(tmp) / "two").exists())
                self.assertEqual(second.metadata["risk_budget"]["reason"], "action-limit")
            finally:
                registry.close()

    def test_safe_runtime_delegation_charges_budget_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = ToolRegistry(root_dir=tmp)
            try:
                registry.policy.autonomy = AutonomyPolicy.from_dict({
                    "level": "balanced",
                    "max_auto_actions_per_turn": 1,
                    "max_auto_risk_per_turn": 1,
                })
                runtime = SafeRuntime(tmp, "ws", "conv", tool_registry=registry)
                first = runtime.execute(
                    "repo.create_directory", {"path": "one"}, turn_id="turn",
                    effective_capabilities={CAP_REPO_WRITE},
                )
                self.assertTrue(first.success, first.error)
                self.assertEqual(first.metadata["risk_budget"]["actions_used"], 1)
                second = runtime.execute(
                    "repo.create_directory", {"path": "two"}, turn_id="turn",
                    effective_capabilities={CAP_REPO_WRITE},
                )
                self.assertFalse(second.success)
                self.assertTrue(second.requires_approval)
                self.assertEqual(second.metadata["risk_budget"]["reason"], "action-limit")
            finally:
                registry.close()


if __name__ == "__main__":
    unittest.main()
