from __future__ import annotations

from kitt.goals.contract_execution import (
    contract_evidence,
    contract_history_paths,
    run_contract_checks,
    run_contract_validation,
)
from kitt.goals.evidence import EvidenceLedger
from kitt.goals.review_runtime import GoalReviewRunner, redact_for_review
from kitt.goals.review_snapshot import (
    MAX_REVIEW_FILES,
    collect_review_snapshot,
    is_reviewable_path,
)


class GoalStepVerifier:
    """Finalize deterministic, adversarial and contract verification."""

    COMPLETION_STATE_TTL_SECONDS = 24 * 60 * 60

    def __init__(self, reviewer_factory=None):
        self.review_runner = GoalReviewRunner(reviewer_factory)

    @staticmethod
    def _review_paths(
        runtime,
        goal,
        contract_item,
        prior_review_paths,
        current_review_paths,
    ):
        history_paths = contract_history_paths(runtime, goal, contract_item)
        paths = [*prior_review_paths, *current_review_paths]
        if contract_item is not None and contract_item.kind == "final":
            paths = [
                *prior_review_paths,
                *history_paths,
                *current_review_paths,
            ]
        review_paths = [
            path
            for path in dict.fromkeys(paths)
            if is_reviewable_path(path)
        ]
        return review_paths, history_paths

    @staticmethod
    def _snapshot(runtime, goal, security, turn_id, review_paths):
        if not review_paths:
            return "", True
        snapshot, complete = collect_review_snapshot(
            runtime,
            goal,
            security,
            turn_id,
            review_paths,
        )
        if snapshot:
            return snapshot, complete
        return (
            "[REVIEW SNAPSHOT UNAVAILABLE FOR RECORDED MUTATED PATHS]",
            False,
        )

    @staticmethod
    def _validate_contract(
        *,
        runtime,
        goal,
        contract_item,
        contract_check_result,
        completion,
        verification,
        result,
        review_paths,
        snapshot,
    ):
        if contract_item is None or not verification.success:
            return verification
        validation, tokens, cost, redactions = run_contract_validation(
            runtime,
            goal,
            contract_item,
            check_result=contract_check_result,
            review_paths=review_paths,
            snapshot=snapshot,
            redact=redact_for_review,
        )
        result["tokens"] += tokens
        result["cost"] += cost
        result["validation_redactions"] = redactions
        result["contract_validation"] = {
            "verdict": validation.verdict,
            "evidence": list(validation.evidence),
            "issues": list(validation.issues),
        }
        return completion.include_validation(verification, validation)

    def _persist_failure(
        self,
        *,
        completion,
        completion_state,
        verification,
        state,
        completion_key,
        result,
        contract_item,
        review_paths,
    ) -> None:
        next_state = completion.next_state(completion_state, verification)
        next_state["review_paths"] = review_paths[:MAX_REVIEW_FILES]
        state.set(
            completion_key,
            next_state,
            ttl_seconds=self.COMPLETION_STATE_TTL_SECONDS,
        )
        if next_state.get("review_exhausted"):
            status = "REVIEW_EXHAUSTED"
            error = (
                "Adversarial review correction budget exhausted after "
                f"{next_state.get('review_cycles')} cycle(s). "
                + verification.feedback
            )
        elif next_state.get("stagnation_exhausted"):
            status = "STAGNATION_EXHAUSTED"
            error = (
                "Autonomous correction stopped because the same failure "
                "repeated without measurable improvement. "
                + verification.feedback
            )
        else:
            status = (
                "ITEM_EXHAUSTED"
                if contract_item is not None
                and contract_item.attempts >= contract_item.max_attempts
                else "INCOMPLETE"
            )
            error = verification.feedback
        result.update(
            status=status,
            error=error,
            stagnated=bool(next_state.get("stagnated")),
            completion_iteration=int(next_state.get("iteration", 0)),
            review_cycles=int(next_state.get("review_cycles", 0)),
        )

    @staticmethod
    def _persist_success(
        *,
        state,
        completion_key,
        resume,
        resume_key,
        result,
        contract_item,
        review_paths,
        verification,
    ) -> None:
        state.delete(completion_key)
        if resume is not None:
            state.delete(resume_key)
        if contract_item is None:
            return
        result["status"] = "ITEM_DONE"
        result["contract_item_id"] = contract_item.id
        result["contract_evidence"] = contract_evidence(
            review_paths,
            max_paths=MAX_REVIEW_FILES,
            host_checks=result.get("contract_checks", {}),
            validation=result.get("contract_validation", {}),
            verification=verification,
        )

    def finalize(
        self,
        *,
        runtime,
        goal,
        contract_item,
        step_goal,
        completion,
        completion_state,
        state,
        completion_key,
        resume,
        resume_key,
        result,
        security,
        turn_id,
        prior_review_paths,
        current_review_paths,
    ) -> dict:
        review_paths, history_paths = self._review_paths(
            runtime,
            goal,
            contract_item,
            prior_review_paths,
            current_review_paths,
        )
        verification = completion.verify(step_goal, result["response"])
        verification, check_result, check_evidence = run_contract_checks(
            runtime,
            goal,
            contract_item,
            review_paths=review_paths,
            history_paths=history_paths,
            completion=completion,
            verification=verification,
        )
        if check_evidence:
            result["contract_checks"] = check_evidence

        risk_name = ""
        snapshot = ""
        if verification.success and review_paths:
            snapshot, snapshot_complete = self._snapshot(
                runtime,
                goal,
                security,
                turn_id,
                review_paths,
            )
            verification, risk_name = self.review_runner.apply(
                runtime=runtime,
                goal=goal,
                step_goal=step_goal,
                completion=completion,
                completion_state=completion_state,
                result=result,
                verification=verification,
                review_paths=review_paths,
                snapshot=snapshot,
                snapshot_complete=snapshot_complete,
                turn_id=turn_id,
            )

        verification = self._validate_contract(
            runtime=runtime,
            goal=goal,
            contract_item=contract_item,
            contract_check_result=check_result,
            completion=completion,
            verification=verification,
            result=result,
            review_paths=review_paths,
            snapshot=snapshot,
        )
        result["verification"] = verification.to_dict()
        result["evidence"] = EvidenceLedger.from_verification(
            verification,
            review_risk=risk_name,
        ).to_dict()

        if verification.success:
            self._persist_success(
                state=state,
                completion_key=completion_key,
                resume=resume,
                resume_key=resume_key,
                result=result,
                contract_item=contract_item,
                review_paths=review_paths,
                verification=verification,
            )
        else:
            self._persist_failure(
                completion=completion,
                completion_state=completion_state,
                verification=verification,
                state=state,
                completion_key=completion_key,
                result=result,
                contract_item=contract_item,
                review_paths=review_paths,
            )
        return result
