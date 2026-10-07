from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable

from kitt.goals.completion import completion_state_key
from kitt.goals.contract_checks import ContractCheckResult, ContractCheckRunner
from kitt.goals.contract_validation import ContractValidationReport, ContractValidator


@dataclass(frozen=True)
class ContractStep:
    item: Any
    goal: Any
    completion_key: str
    completion_state: dict[str, Any] | None


def prepare_contract_step(runtime, goal, state) -> ContractStep:
    item = runtime.goals.current_item(goal.id)
    if item is None:
        key = completion_state_key(goal.id)
        return ContractStep(None, goal, key, state.get(key))

    step_goal = replace(
        goal,
        objective=item.prompt,
        success_criteria=list(item.criteria),
        gates=[],
    )
    key = completion_state_key(f"{goal.id}:{item.local_id}")
    completion_state = state.get(key)
    if item.attempts == 1 and item.last_feedback is None and completion_state is not None:
        state.delete(key)
        completion_state = None
    return ContractStep(item, step_goal, key, completion_state)


def build_contract_prompt(runtime, goal, step: ContractStep, resume) -> str:
    prompt = step.goal.objective
    if step.item is not None:
        progress = " | ".join(
            f"{item.local_id}:{item.status}"
            for item in runtime.goals.contract_items(goal.id)
        )
        prompt = (
            f"{prompt}\n\n[KITT CONTRACT PROGRESS]\n{progress}\n"
            "Work only on the current item. Do not redo DONE items."
        )
    if isinstance(resume, dict):
        approved_output = str(resume.get("tool_output") or "")[:32768]
        prompt = (
            "Continue the existing persistent goal after an approved host action. "
            "The approved action already succeeded; do not repeat it. "
            "Use only the remaining work for the current contract item.\n\n"
            f"Approved host result:\n{approved_output}\n\n"
            f"Current objective:\n{prompt}"
        )
    return prompt


def contract_history_paths(runtime, goal, item) -> list[str]:
    if item is None:
        return []
    paths: list[str] = []
    for previous in runtime.goals.contract_items(goal.id):
        if previous.status == "DONE":
            paths.extend(list(previous.evidence.get("changed_paths") or []))
    return list(dict.fromkeys(str(path) for path in paths if str(path).strip()))


def run_contract_checks(
    runtime,
    goal,
    item,
    *,
    review_paths: list[str],
    history_paths: list[str],
    completion,
    verification,
) -> tuple[Any, ContractCheckResult | None, dict[str, Any]]:
    if item is None:
        return verification, None, {}

    verification_paths = list(
        dict.fromkeys(
            [
                *item.paths,
                *(history_paths if item.kind == "final" else []),
                *review_paths,
            ]
        )
    )
    check_result = ContractCheckRunner(runtime).run(
        goal,
        item,
        verification_paths,
    )
    verification = completion.include_checks(
        verification,
        check_result.checks,
    )
    return verification, check_result, check_result.evidence


def run_contract_validation(
    runtime,
    goal,
    item,
    *,
    check_result: ContractCheckResult | None,
    review_paths: list[str],
    snapshot: str,
    redact: Callable[[Any, str], tuple[str, tuple[str, ...], int]],
) -> tuple[ContractValidationReport, int, float, int]:
    validation_snapshot, _, snapshot_redactions = redact(runtime, snapshot)
    deterministic_text = (
        check_result.evidence_text
        if check_result is not None
        else "No host verification step was applicable."
    )
    deterministic_text, _, evidence_redactions = redact(runtime, deterministic_text)
    validation, tokens, cost = ContractValidator(runtime).validate(
        goal=goal,
        item=item,
        deterministic_evidence=deterministic_text,
        changed_paths=review_paths,
        snapshot=validation_snapshot,
    )
    return (
        validation,
        tokens,
        cost,
        snapshot_redactions + evidence_redactions,
    )


def contract_evidence(
    review_paths: list[str],
    *,
    max_paths: int,
    host_checks: dict[str, Any],
    validation: dict[str, Any],
    verification,
) -> dict[str, Any]:
    return {
        "changed_paths": review_paths[:max_paths],
        "host_checks": host_checks,
        "validation": validation,
        "verification": {
            "success": bool(verification.success),
            "score": float(verification.score),
            "checks": [
                {
                    "kind": check.kind,
                    "name": check.name,
                    "passed": bool(check.passed),
                }
                for check in verification.checks
            ],
        },
    }
