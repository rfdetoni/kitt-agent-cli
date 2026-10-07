from __future__ import annotations

from kitt.goals.contract import ContractPlanner
from kitt.security.capabilities import (
    CAP_ARTIFACT_READ,
    CAP_ARTIFACT_WRITE,
    CAP_PROCESS_RUN,
    CAP_REPO_READ,
    CAP_REPO_SEARCH,
    CAP_REPO_WRITE,
)


def contract_capabilities(value: str | None) -> list[str]:
    capabilities = {CAP_REPO_READ, CAP_REPO_SEARCH, CAP_ARTIFACT_READ}
    raw = {
        item.strip().lower()
        for item in str(value or "").replace(";", ",").split(",")
        if item.strip()
    }
    unknown = raw - {"read", "write", "run", "all"}
    if unknown:
        raise ValueError(f"Unknown --allow values: {sorted(unknown)}")
    if "all" in raw:
        raw.update({"write", "run"})
    if "write" in raw:
        capabilities.update({CAP_REPO_WRITE, CAP_ARTIFACT_WRITE})
    if "run" in raw:
        capabilities.add(CAP_PROCESS_RUN)
    return sorted(capabilities)


def create_planned_contract(
    runtime,
    *,
    conversation_id: str,
    objective: str,
    allow: str | None = None,
    max_attempts: int = 5,
    token_budget: int | None = None,
    max_wall_seconds: int = 1800,
    start_paused: bool = True,
):
    items = ContractPlanner(runtime).plan(conversation_id, objective)
    goal = runtime.goals.create_contract(
        conversation_id,
        objective,
        items,
        contract_capabilities(allow),
        token_budget,
        max_wall_seconds,
        max_attempts,
        start_paused=start_paused,
    )
    return goal, items


def schedule_contract(runtime, goal_id: str) -> bool:
    return runtime.goal_scheduler.schedule_goal(
        goal_id,
        heartbeat_enabled=True,
        resume_policy="auto",
    )


def resume_contract(runtime, goal_id: str, *, conversation_id: str | None = None):
    item = runtime.goals.resume_contract(goal_id, conversation_id=conversation_id)
    if item is None:
        return None
    if not schedule_contract(runtime, goal_id):
        raise RuntimeError(f"Unable to schedule contract goal {goal_id}")
    return runtime.goals.get(goal_id)


def render_contract(runtime, goal_id: str) -> str:
    goal = runtime.goals.get(goal_id)
    if goal is None:
        return "Contract goal not found."
    items = runtime.goals.contract_items(goal_id)
    if not items:
        return f"Goal {goal_id} has no task contract."
    lines = [
        f"Contract {goal.id} [{goal.state}]",
        f"Objective: {goal.objective}",
        f"Budget: {goal.turns_used}/{goal.max_turns} scheduler attempts; "
        f"{goal.tokens_used} tokens; ${goal.cost_used:.4f}",
    ]
    for item in items:
        lines.append(
            f"{item.position + 1:02d}. [{item.status}] {item.local_id} "
            f"{item.title} (attempts {item.attempts}/{item.max_attempts})"
        )
        if item.last_feedback:
            lines.append(f"    feedback: {item.last_feedback[:500]}")
    return "\n".join(lines)
