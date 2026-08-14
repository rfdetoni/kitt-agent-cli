import unittest
from kitt.router.models import TaskFeatures, ModelCapabilities, RoutingDecision
from kitt.router.features import TaskFeatureExtractor
from kitt.router.policy import RoutingPolicy
from kitt.router.escalation import EscalationManager

class TestAdaptiveRouting(unittest.TestCase):
    def test_feature_extraction_pt_and_en(self):
        f1 = TaskFeatureExtractor.extract("Corrigir exceção e bug em kitt/core/turn_processor.py")
        self.assertEqual(f1.intent, "DEBUG")
        self.assertEqual(f1.risk, "MEDIUM")
        self.assertIn("kitt/core/turn_processor.py", f1.paths)

        f2 = TaskFeatureExtractor.extract("Explain how routing works")
        self.assertEqual(f2.intent, "READ")
        self.assertEqual(f2.risk, "LOW")

    def test_routing_policy_hard_constraints_and_scoring(self):
        policy = RoutingPolicy()
        features = TaskFeatureExtractor.extract("Fix bug in app.py")

        caps = {
            "local_small": ModelCapabilities(
                profile_name="local_small", tier="small", input_context_limit=8192, max_output_tokens=2048,
                supports_json=True, supports_native_tools=True, tool_call_reliability=0.85, code_edit_score=0.8,
                reasoning_score=0.75, languages=("py",), is_local=True, privacy_class="local"
            ),
            "cloud_large": ModelCapabilities(
                profile_name="cloud_large", tier="large", input_context_limit=32768, max_output_tokens=4096,
                supports_json=True, supports_native_tools=True, tool_call_reliability=0.95, code_edit_score=0.95,
                reasoning_score=0.95, languages=("py",), is_local=False, privacy_class="cloud"
            )
        }

        # Under local_only, cloud profile is excluded
        d_local = policy.select_route(features, caps, privacy_mode="local_only")
        self.assertEqual(d_local.selected_profile, "local_small")

        # Under cloud_allowed for a simple bug, local_small is preferred by score
        d_cloud = policy.select_route(features, caps, privacy_mode="cloud_allowed")
        self.assertEqual(d_cloud.selected_profile, "local_small")

    def test_user_override_cannot_bypass_privacy(self):
        policy = RoutingPolicy()
        features = TaskFeatureExtractor.extract("Fix bug in app.py")
        caps = {
            "cloud_large": ModelCapabilities(
                profile_name="cloud_large", tier="large", input_context_limit=32768, max_output_tokens=4096,
                supports_json=True, supports_native_tools=True, tool_call_reliability=0.95, code_edit_score=0.95,
                reasoning_score=0.95, languages=("py",), is_local=False, privacy_class="cloud"
            )
        }

        decision = policy.select_route(
            features,
            caps,
            privacy_mode="offline",
            user_override_profile="cloud_large",
        )

        self.assertEqual(decision.privacy_decision, "BLOCKED")
        self.assertEqual(decision.selected_profile, "")

    def test_no_eligible_profile_blocks_instead_of_unsafe_fallback(self):
        policy = RoutingPolicy()
        features = TaskFeatureExtractor.extract("Fix bug in app.py")
        caps = {
            "cloud_large": ModelCapabilities(
                profile_name="cloud_large", tier="large", input_context_limit=32768, max_output_tokens=4096,
                supports_json=True, supports_native_tools=True, tool_call_reliability=0.95, code_edit_score=0.95,
                reasoning_score=0.95, languages=("py",), is_local=False, privacy_class="cloud"
            )
        }

        decision = policy.select_route(features, caps, privacy_mode="local_only")

        self.assertEqual(decision.privacy_decision, "BLOCKED")
        self.assertEqual(decision.selected_profile, "")

    def test_component_scores_include_final_tier_bonus(self):
        policy = RoutingPolicy()
        features = TaskFeatureExtractor.extract("Explain app.py")
        caps = {
            "local_small": ModelCapabilities(
                profile_name="local_small", tier="small", input_context_limit=8192, max_output_tokens=2048,
                supports_json=True, supports_native_tools=True, tool_call_reliability=0.8, code_edit_score=0.8,
                reasoning_score=0.8, languages=("py",), is_local=True, privacy_class="local"
            ),
            "local_large": ModelCapabilities(
                profile_name="local_large", tier="large", input_context_limit=32768, max_output_tokens=4096,
                supports_json=True, supports_native_tools=True, tool_call_reliability=0.8, code_edit_score=0.8,
                reasoning_score=0.8, languages=("py",), is_local=True, privacy_class="local"
            ),
        }

        decision = policy.select_route(features, caps, privacy_mode="local_only")

        self.assertGreater(decision.component_scores["local_small"], decision.component_scores["local_large"])
        self.assertEqual(decision.context_profile, "local_small")

    def test_escalation_state_machine_and_handoff(self):
        mgr = EscalationManager(max_small_retries=1)
        self.assertEqual(mgr.state, "SMALL_ATTEMPT")

        state = mgr.record_attempt(success=False, error="Invalid patch syntax", tokens_in=500, tokens_out=100)
        self.assertEqual(state, "RETRY_SMALL")

        state = mgr.record_attempt(success=False, error="Second patch syntax error", tokens_in=500, tokens_out=100)
        self.assertEqual(state, "ESCALATE_LARGE")

        mgr.add_fact("Found bug in line 42")
        mgr.add_tool_result("read_file", True, "Read lines 30-50")
        handoff = mgr.create_handoff("Fix bug in line 42")

        self.assertEqual(handoff.original_task, "Fix bug in line 42")
        self.assertEqual(len(handoff.verified_facts), 1)
        self.assertEqual(len(handoff.errors), 2)
        self.assertEqual(handoff.input_tokens_spent, 1000)

if __name__ == "__main__":
    unittest.main()
