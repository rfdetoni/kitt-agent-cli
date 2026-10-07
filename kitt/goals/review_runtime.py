from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from kitt.context_filter.prompt_budget import TokenCounter
from kitt.goals.review import AdversarialCodeReviewer, combine_adversarial_reviews
from kitt.goals.risk import ReviewRisk, classify_review_risk
from kitt.llm.client import LLMClient
from kitt.llm.privacy import profile_processing_is_local
from kitt.metrics.cost_estimator import estimate_cost, estimate_execution_cost


def redact_for_review(runtime, text: str):
    scanner = getattr(runtime, "sensitive_scanner", None)
    if scanner is None:
        return text, (), 0
    result = scanner.scan_and_redact(text)
    return result.clean_text, tuple(result.categories), int(result.redaction_count)


class ReviewModelCall:
    """One reviewer model call with egress, budget and usage accounting."""

    def __init__(
        self,
        *,
        runtime,
        goal,
        profile_name,
        profile,
        route,
        pass_index,
        iteration,
        turn_id,
        usage,
    ):
        self.runtime = runtime
        self.goal = goal
        self.profile_name = profile_name
        self.profile = profile
        self.route = route
        self.pass_index = pass_index
        self.iteration = iteration
        self.turn_id = str(turn_id or "")
        self.usage = usage

    def _prepare_input(self, system_prompt: str, user_prompt: str):
        clean_system, system_categories, system_redactions = redact_for_review(
            self.runtime,
            system_prompt,
        )
        clean_user, user_categories, user_redactions = redact_for_review(
            self.runtime,
            user_prompt,
        )
        categories = tuple(
            sorted(set(system_categories) | set(user_categories))
        )
        redactions = system_redactions + user_redactions
        input_tokens = (
            TokenCounter.count_tokens(clean_system)
            + TokenCounter.count_tokens(clean_user)
        )
        return clean_system, clean_user, categories, redactions, input_tokens

    def _authorize_egress(
        self,
        *,
        clean_system,
        clean_user,
        categories,
        redactions,
        input_tokens,
    ) -> None:
        if profile_processing_is_local(self.profile):
            return
        backend = str(
            getattr(self.profile, "backend", "") or ""
        ).strip().lower()
        host = urlparse(
            str(getattr(self.profile, "base_url", "") or "")
        ).hostname
        policy = getattr(self.runtime, "egress_policy", None)
        if policy is None:
            raise PermissionError(
                "Remote adversarial review requires EgressPolicy"
            )
        allowed, _manifest, reason = policy.evaluate_egress(
            host=host or backend or "remote-provider",
            is_local=False,
            provider=backend,
            model=str(getattr(self.profile, "model", "") or ""),
            workspace_id=self.runtime.workspace_id,
            bytes_out=len((clean_system + clean_user).encode("utf-8")),
            estimated_tokens=input_tokens,
            sensitive_categories=categories,
            redaction_count=redactions,
        )
        if not allowed:
            raise PermissionError(reason)

    def _budget(self):
        return getattr(
            self.runtime.processor,
            "execution_budgets",
            {},
        ).get(self.turn_id)

    def _estimate_input_cost(self, input_tokens: int) -> float:
        return estimate_execution_cost(
            str(getattr(self.profile, "model", "") or ""),
            input_tokens,
            0,
            backend=str(getattr(self.profile, "backend", "") or ""),
            workspace_root=str(self.runtime.canonical_root),
        ).estimated_usd

    def _record_usage(
        self,
        *,
        response,
        input_tokens,
        provider_usage,
        estimated_input_cost,
        budget,
        redactions,
    ) -> None:
        output_tokens = TokenCounter.count_tokens(response)
        raw_input = provider_usage.get("prompt_tokens")
        raw_output = provider_usage.get("completion_tokens")
        actual_input = (
            int(raw_input)
            if isinstance(raw_input, (int, float))
            and not isinstance(raw_input, bool)
            else input_tokens
        )
        actual_output = (
            int(raw_output)
            if isinstance(raw_output, (int, float))
            and not isinstance(raw_output, bool)
            else output_tokens
        )
        if budget is not None:
            actual_input_cost = self._estimate_input_cost(actual_input)
            output_cost = estimate_execution_cost(
                str(getattr(self.profile, "model", "") or ""),
                0,
                actual_output,
                backend=str(getattr(self.profile, "backend", "") or ""),
                workspace_root=str(self.runtime.canonical_root),
            ).estimated_usd
            budget.reconcile_model_input(
                estimated_tokens=input_tokens,
                actual_tokens=actual_input,
                estimated_cost=estimated_input_cost,
                actual_cost=actual_input_cost,
                stage=self.route,
            )
            budget.record_model_output(
                output_tokens=actual_output,
                cost=output_cost,
                stage=self.route,
            )
        cost = estimate_cost(
            str(getattr(self.profile, "model", "") or ""),
            actual_input,
            actual_output,
            workspace_root=str(self.runtime.canonical_root),
        )
        self.usage["tokens"] += actual_input + actual_output
        self.usage["cost"] += float(cost.estimated_usd)
        self.usage["profile"] = self.profile_name
        self.usage["model"] = str(
            getattr(self.profile, "model", "") or ""
        )
        self.usage["redactions"] += redactions

    def __call__(self, system_prompt: str, user_prompt: str) -> str:
        (
            clean_system,
            clean_user,
            categories,
            redactions,
            input_tokens,
        ) = self._prepare_input(system_prompt, user_prompt)
        self._authorize_egress(
            clean_system=clean_system,
            clean_user=clean_user,
            categories=categories,
            redactions=redactions,
            input_tokens=input_tokens,
        )

        budget = self._budget()
        estimated_input_cost = self._estimate_input_cost(input_tokens)
        if budget is not None:
            budget.reserve_model_call(
                input_tokens=input_tokens,
                cost=estimated_input_cost,
                stage=self.route,
            )
        provider_usage: dict[str, object] = {}

        def observe_usage(value):
            provider_usage.clear()
            provider_usage.update(dict(value or {}))

        def reserve_retry(attempt):
            if budget is not None and int(attempt) > 0:
                budget.reserve_model_call(
                    input_tokens=input_tokens,
                    cost=estimated_input_cost,
                    stage=self.route,
                )

        with LLMClient(self.profile) as client:
            response = client.chat(
                [{"role": "user", "content": clean_user}],
                system_prompt=clean_system,
                session_key=(
                    f"goal-review:{self.goal.id}:{self.iteration}:"
                    f"{self.route}:{self.pass_index}"
                ),
                usage_callback=observe_usage,
                attempt_callback=reserve_retry,
            )
        self._record_usage(
            response=response,
            input_tokens=input_tokens,
            provider_usage=provider_usage,
            estimated_input_cost=estimated_input_cost,
            budget=budget,
            redactions=redactions,
        )
        return response


class GoalReviewRunner:
    """Run risk-gated adversarial review for one goal step."""

    def __init__(self, reviewer_factory=None):
        self.reviewer_factory = reviewer_factory

    @staticmethod
    def _profile(runtime, route: str):
        router = runtime.processor.router
        configured = str(router.config.routing.get(route) or "").strip()
        if not configured and route != "adversarial-review":
            configured = str(
                router.config.routing.get("adversarial-review") or ""
            ).strip()
        if configured and configured in router.config.profiles:
            return configured, router.config.profiles[configured]
        return router.resolve_profile_for_task("code-generation")

    def _reviewer(
        self,
        *,
        runtime,
        goal,
        completion_state,
        usage,
        route,
        pass_index,
        turn_id,
    ):
        if self.reviewer_factory is not None:
            return self.reviewer_factory(
                runtime,
                goal,
                completion_state,
                usage,
            )

        profile_name, profile = self._profile(runtime, route)
        execution_name, execution_profile = (
            runtime.processor.router.resolve_profile_for_task(
                "code-generation"
            )
        )
        reviewer_identity = (
            str(getattr(profile, "backend", "") or "").casefold(),
            str(getattr(profile, "model", "") or ""),
            str(getattr(profile, "base_url", "") or ""),
        )
        execution_identity = (
            str(getattr(execution_profile, "backend", "") or "").casefold(),
            str(getattr(execution_profile, "model", "") or ""),
            str(getattr(execution_profile, "base_url", "") or ""),
        )
        usage["route"] = route
        usage["independent"] = reviewer_identity != execution_identity
        usage["execution_profile"] = execution_name
        iteration = int(
            (completion_state or {}).get("iteration", 0) or 0
        ) + 1
        review_call = ReviewModelCall(
            runtime=runtime,
            goal=goal,
            profile_name=profile_name,
            profile=profile,
            route=route,
            pass_index=pass_index,
            iteration=iteration,
            turn_id=turn_id,
            usage=usage,
        )
        return AdversarialCodeReviewer(review_call)

    @staticmethod
    def _record_budget_snapshot(runtime, turn_id: str) -> None:
        budget = getattr(
            runtime.processor,
            "execution_budgets",
            {},
        ).get(turn_id)
        if budget is None:
            return
        snapshots = getattr(
            runtime.processor,
            "execution_budget_snapshots",
            None,
        )
        if snapshots is None:
            snapshots = {}
            runtime.processor.execution_budget_snapshots = snapshots
        snapshots[turn_id] = budget.snapshot()

    @staticmethod
    def _publish(runtime, goal, review, assessment, review_usage, reviews):
        events = getattr(runtime, "events", None)
        if events is None:
            return
        events.publish(
            "GoalAdversarialReviewCompleted",
            {
                "goal_id": goal.id,
                "approved": review.approved,
                "status": review.status,
                "risk": assessment.name,
                "review_passes": len(reviews),
                "required_findings": sum(
                    1 for item in review.findings if item.required
                ),
                "profiles": [
                    item.get("profile")
                    for item in review_usage["passes"]
                ],
                "models": [
                    item.get("model")
                    for item in review_usage["passes"]
                ],
            },
        )

    def apply(
        self,
        *,
        runtime,
        goal,
        step_goal,
        completion,
        completion_state,
        result,
        verification,
        review_paths,
        snapshot,
        snapshot_complete,
        turn_id,
    ):
        assessment = classify_review_risk(review_paths, snapshot)
        result["review_risk"] = assessment.to_dict()
        if assessment.level == ReviewRisk.LOW or not snapshot:
            result["review"] = {
                "applicable": False,
                "approved": True,
                "status": "SKIPPED_LOW_RISK",
                "risk": assessment.name,
                "summary": (
                    "Deterministic verification is authoritative "
                    "for this low-risk change."
                ),
            }
            return verification, assessment.name

        review_usage: dict[str, Any] = {
            "tokens": 0,
            "cost": 0.0,
            "redactions": 0,
            "passes": [],
        }
        reviews = []
        pass_count = 2 if assessment.level == ReviewRisk.CRITICAL else 1
        for pass_index in range(1, pass_count + 1):
            route = (
                "critical-review"
                if pass_index == 2
                else "adversarial-review"
            )
            pass_usage: dict[str, Any] = {
                "tokens": 0,
                "cost": 0.0,
                "redactions": 0,
            }
            reviewer = self._reviewer(
                runtime=runtime,
                goal=step_goal,
                completion_state=completion_state,
                usage=pass_usage,
                route=route,
                pass_index=pass_index,
                turn_id=turn_id,
            )
            review = reviewer.review(
                objective=step_goal.objective,
                success_criteria=list(
                    getattr(step_goal, "success_criteria", None) or []
                ),
                verification=verification,
                change_snapshot=snapshot,
                previous_feedback=str(
                    (completion_state or {}).get("feedback") or ""
                ),
                snapshot_complete=snapshot_complete,
                risk_level=assessment.name,
            )
            reviews.append(review)
            review_usage["tokens"] += int(
                pass_usage.get("tokens", 0) or 0
            )
            review_usage["cost"] += float(
                pass_usage.get("cost", 0.0) or 0.0
            )
            review_usage["redactions"] += int(
                pass_usage.get("redactions", 0) or 0
            )
            review_usage["passes"].append(
                {
                    "profile": pass_usage.get("profile"),
                    "model": pass_usage.get("model"),
                    "approved": review.approved,
                    "status": review.status,
                }
            )
            if not review.approved:
                break

        review = combine_adversarial_reviews(reviews)
        result["review"] = review.to_dict()
        result["tokens"] += int(review_usage.get("tokens", 0) or 0)
        result["cost"] += float(review_usage.get("cost", 0.0) or 0.0)
        result["review_usage"] = review_usage
        self._record_budget_snapshot(runtime, turn_id)
        verification = completion.include_adversarial_review(
            verification,
            review,
        )
        self._publish(
            runtime,
            goal,
            review,
            assessment,
            review_usage,
            reviews,
        )
        return verification, assessment.name
