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

    def _eligibility_reason(
        self,
        features: TaskFeatures,
        cap: ModelCapabilities,
        privacy_mode: str,
    ) -> Optional[str]:
        if privacy_mode in ("offline", "local_only") and not cap.is_local:
            return f"profile '{cap.profile_name}' blocked by privacy mode '{privacy_mode}'"
        if cap.health == "unhealthy":
            return f"profile '{cap.profile_name}' is unhealthy"
        if features.expected_context_tokens > cap.input_context_limit:
            return f"profile '{cap.profile_name}' context window too small"
        if features.requires_tools and not (cap.supports_native_tools or cap.supports_json):
            return f"profile '{cap.profile_name}' lacks required tool support"
        return None

    def _blocked_decision(
        self,
        privacy_mode: str,
        reasons: List[str],
        scores: Dict[str, float],
    ) -> RoutingDecision:
        return RoutingDecision(
            route_id=uuid.uuid4().hex[:12],
            selected_profile="",
            selected_tier="",
            context_profile=None,
            reasons=tuple(reasons),
            component_scores=scores,
            escalation_conditions=("adjust_privacy_mode", "select_eligible_profile"),
            privacy_mode=privacy_mode,
            privacy_decision="BLOCKED",
            policy_version=self.policy_version,
            created_at=str(time.time())
        )

    def _context_profile(
        self,
        selected: ModelCapabilities,
        eligible: List[Tuple[ModelCapabilities, float]],
    ) -> str:
        for cap, _score in eligible:
            if cap.tier == "small":
                return cap.profile_name
        return selected.profile_name

    def select_route(
        self,
        features: TaskFeatures,
        capabilities: Dict[str, ModelCapabilities],
        privacy_mode: str = "hybrid_redacted",
        user_override_profile: Optional[str] = None
    ) -> RoutingDecision:
        reasons: List[str] = []
        scores: Dict[str, float] = {}

        if user_override_profile:
            chosen = capabilities.get(user_override_profile)
            if not chosen:
                reasons.append(f"User override profile '{user_override_profile}' not found")
                return self._blocked_decision(privacy_mode, reasons, scores)
            reason = self._eligibility_reason(features, chosen, privacy_mode)
            if reason:
                reasons.append(reason)
                return self._blocked_decision(privacy_mode, reasons, scores)

        eligible: List[Tuple[ModelCapabilities, float]] = []

        for name, cap in capabilities.items():
            reason = self._eligibility_reason(features, cap, privacy_mode)
            if reason:
                reasons.append(reason)
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

            # Prefer small tier for low/medium complexity, small risk
            if features.complexity in ("LOW", "MEDIUM") and features.risk in ("LOW", "MEDIUM") and cap.tier == "small":
                final_score += 0.2

            # Prefer large tier for high complexity / critical risk
            if (features.complexity == "HIGH" or features.risk == "CRITICAL" or features.cross_module) and cap.tier == "large":
                final_score += 0.3

            scores[name] = round(final_score, 3)
            eligible.append((cap, final_score))

        if not eligible:
            reasons.append("No eligible profile available")
            return self._blocked_decision(privacy_mode, reasons, scores)

        eligible.sort(key=lambda x: x[1], reverse=True)
        if user_override_profile:
            selected_cap = capabilities[user_override_profile]
            best_score = scores.get(selected_cap.profile_name, 1.0)
            reasons.append(f"User override specified profile '{user_override_profile}'")
        else:
            selected_cap, best_score = eligible[0]

        reasons.append(
            f"Selected profile '{selected_cap.profile_name}' (tier={selected_cap.tier}, score={best_score}) "
            f"for intent={features.intent}, complexity={features.complexity}, risk={features.risk}"
        )

        return RoutingDecision(
            route_id=uuid.uuid4().hex[:12],
            selected_profile=selected_cap.profile_name,
            selected_tier=selected_cap.tier,
            context_profile=self._context_profile(selected_cap, eligible),
            reasons=tuple(reasons),
            component_scores=scores,
            escalation_conditions=("small_attempt_failed", "patch_invalid", "validation_failed"),
            privacy_mode=privacy_mode,
            privacy_decision="ALLOWED",
            policy_version=self.policy_version,
            created_at=str(time.time())
        )
