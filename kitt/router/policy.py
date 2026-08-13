"""Deterministic policy for model selection and route scoring."""

from __future__ import annotations

import time
import uuid
from typing import Dict, List, Tuple, Optional
from kitt.router.models import TaskFeatures, ModelCapabilities, RoutingDecision

class RoutingPolicy:
    """Evaluates hard safety constraints and scores candidate profiles to select optimal model."""

    def __init__(self, policy_version: str = "v2.0"):
        self.policy_version = policy_version

    def select_route(
        self,
        features: TaskFeatures,
        capabilities: Dict[str, ModelCapabilities],
        privacy_mode: str = "hybrid_redacted",
        user_override_profile: Optional[str] = None
    ) -> RoutingDecision:
        reasons: List[str] = []
        scores: Dict[str, float] = {}

        if user_override_profile and user_override_profile in capabilities:
            chosen = capabilities[user_override_profile]
            reasons.append(f"User override specified profile '{user_override_profile}'")
            return RoutingDecision(
                route_id=uuid.uuid4().hex[:12],
                selected_profile=chosen.profile_name,
                selected_tier=chosen.tier,
                context_profile=chosen.profile_name,
                reasons=tuple(reasons),
                component_scores={chosen.profile_name: 1.0},
                escalation_conditions=("validation_failure", "too_many_retries"),
                privacy_mode=privacy_mode,
                privacy_decision="ALLOWED",
                policy_version=self.policy_version,
                created_at=str(time.time())
            )

        eligible: List[Tuple[ModelCapabilities, float]] = []

        for name, cap in capabilities.items():
            # Hard Rule 1: Offline / local_only prohiibt non-local providers
            if privacy_mode in ("offline", "local_only") and not cap.is_local:
                continue

            # Hard Rule 2: Unhealthy profile excluded
            if cap.health == "unhealthy":
                continue

            # Hard Rule 3: Context limit must accommodate prompt
            if features.expected_context_tokens > cap.input_context_limit:
                continue

            # Hard Rule 4: Required tool support
            if features.requires_tools and not (cap.supports_native_tools or cap.supports_json):
                continue

            # Score calculation
            quality_fit = cap.code_edit_score if features.intent == "IMPLEMENT" else cap.reasoning_score
            capability_fit = cap.tool_call_reliability if features.requires_tools else 0.8
            privacy_fit = 1.0 if cap.is_local else (0.7 if privacy_mode == "hybrid_redacted" else 0.9)
            context_fit = 1.0 if cap.input_context_limit >= features.expected_context_tokens * 2 else 0.7

            latency_pen = 0.2 if (cap.tokens_per_second and cap.tokens_per_second < 15.0) else 0.0
            resource_pen = min(0.3, cap.current_load * 0.3)
            recent_fail_pen = 0.3 if cap.health == "degraded" else 0.0

            final_score = (
                quality_fit * 0.35 +
                capability_fit * 0.25 +
                privacy_fit * 0.25 +
                context_fit * 0.15 -
                latency_pen - resource_pen - recent_fail_pen
            )
            scores[name] = round(final_score, 3)

            # Prefer small tier for low/medium complexity, small risk
            if features.complexity in ("LOW", "MEDIUM") and features.risk in ("LOW", "MEDIUM") and cap.tier == "small":
                final_score += 0.2

            # Prefer large tier for high complexity / critical risk
            if (features.complexity == "HIGH" or features.risk == "CRITICAL" or features.cross_module) and cap.tier == "large":
                final_score += 0.3

            eligible.append((cap, final_score))

        if not eligible:
            # Fallback: pick any available profile
            fallback_cap = list(capabilities.values())[0] if capabilities else ModelCapabilities(
                profile_name="fallback", tier="small", input_context_limit=8192, max_output_tokens=2048,
                supports_json=True, supports_native_tools=True, tool_call_reliability=0.8, code_edit_score=0.7,
                reasoning_score=0.7, languages=(), is_local=True, privacy_class="local"
            )
            reasons.append("Fallback route selected (no strictly eligible profile)")
            return RoutingDecision(
                route_id=uuid.uuid4().hex[:12],
                selected_profile=fallback_cap.profile_name,
                selected_tier=fallback_cap.tier,
                context_profile=fallback_cap.profile_name,
                reasons=tuple(reasons),
                component_scores=scores,
                escalation_conditions=("validation_failure",),
                privacy_mode=privacy_mode,
                privacy_decision="FALLBACK",
                policy_version=self.policy_version,
                created_at=str(time.time())
            )

        eligible.sort(key=lambda x: x[1], reverse=True)
        selected_cap, best_score = eligible[0]

        reasons.append(
            f"Selected profile '{selected_cap.profile_name}' (tier={selected_cap.tier}, score={best_score}) "
            f"for intent={features.intent}, complexity={features.complexity}, risk={features.risk}"
        )

        return RoutingDecision(
            route_id=uuid.uuid4().hex[:12],
            selected_profile=selected_cap.profile_name,
            selected_tier=selected_cap.tier,
            context_profile=selected_cap.profile_name,
            reasons=tuple(reasons),
            component_scores=scores,
            escalation_conditions=("small_attempt_failed", "patch_invalid", "validation_failed"),
            privacy_mode=privacy_mode,
            privacy_decision="ALLOWED",
            policy_version=self.policy_version,
            created_at=str(time.time())
        )
