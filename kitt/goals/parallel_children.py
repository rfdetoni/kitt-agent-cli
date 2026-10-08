"""Bounded parallel implementation for independent automatic contract items.

The GoalScheduler remains the only owner of contract transitions and
verification. Child work runs under the current scheduler lease, and every
child in a batch settles before that lease is released.
"""
from __future__ import annotations

import time

from kitt.security.capabilities import CAP_CHILD_SPAWN


_TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT", "WAITING_APPROVAL"}


def _roster_key(goal_id: str) -> str:
    return f"goal.parallel_children:{goal_id}"


def _ready_batch(runtime, goal, current):
    items = runtime.goals.contract_items(goal.id)
    completed = {item.local_id for item in items if item.status == "DONE"}
    chosen = []
    used = set()
    for item in items:
        if item.kind != "task" or item.status not in {"PENDING", "RUNNING"}:
            continue
        if item.id != current.id and item.status != "PENDING":
            continue
        if not item.paths or not set(item.depends_on).issubset(completed):
            continue
        paths = set(item.paths)
        if used.intersection(paths):
            continue
        chosen.append(item)
        used.update(paths)
    # Only batch if the scheduled item itself can run independently.
    return chosen if len(chosen) >= 2 and current.id in {x.id for x in chosen} else []


def parallel_contract_result(runtime, goal, current, state, security):
    """Return a child result to be verified, or None for normal serial execution.

    A batch only runs under the user's autonomous profile. It waits for all
    admitted children before the scheduler releases its goal fencing lease.
    Pending approvals cannot be silently granted to scoped subprocesses.
    """
    if current is None or current.kind != "task":
        return None
    if getattr(runtime.policy.autonomy, "level", "") != "autonomous":
        return None
    if CAP_CHILD_SPAWN not in security.capabilities:
        return None
    children = runtime.children
    key = _roster_key(goal.id)
    roster = dict(state.get(key) or {})
    if current.local_id not in roster:
        if state.get(f"goal.parallel_failed:{goal.id}:{current.local_id}"):
            return None
        batch = _ready_batch(runtime, goal, current)
        if not batch:
            return None
        available = max(1, int(children.max_children))
        if available < 2:
            return None
        launched = []
        for item in batch[:available]:
            try:
                admitted = runtime.registry.execute_tool(
                    "child_spawn",
                    {
                        "name": item.local_id,
                        "task": (
                            item.prompt + "\n\nAcceptance criteria:\n"
                            + "\n".join(f"- {x}" for x in item.criteria)
                            + "\nImplement only the allocated paths. The parent handles build "
                            "and integration checks; do not invoke shell commands."
                        ),
                        "allowed_paths": list(item.paths),
                        "enabled_tools": ["read_file", "search", "repository_map", "write_file", "apply_patch"],
                        "token_budget": max(2048, int(runtime.config.child_token_budget)),
                        "timeout_seconds": float(runtime.config.child_timeout_seconds),
                        "agent_role": "IMPLEMENT",
                    },
                    turn_id=goal.id,
                    conversation_id=goal.conversation_id,
                    workspace_id=runtime.workspace_id,
                    origin="SCHEDULE",
                    security_context=security,
                )
                child_id = str((admitted.metadata or {}).get("child_id") or "")
                if not admitted.success or admitted.requires_approval or not child_id:
                    raise RuntimeError(
                        admitted.error or "Parallel child admission requires approval or was denied"
                    )
            except Exception:
                for child_id in launched:
                    children.cancel(child_id)
                if launched:
                    launched_ids = set(launched)
                    roster = {name: cid for name, cid in roster.items() if cid not in launched_ids}
                    state.set(key, roster, ttl_seconds=24 * 60 * 60)
                    state.set(
                        f"goal.parallel_failed:{goal.id}:{current.local_id}",
                        True, ttl_seconds=24 * 60 * 60,
                    )
                    raise
                return None
            roster[item.local_id] = child_id
            launched.append(child_id)
            state.set(key, roster, ttl_seconds=24 * 60 * 60)

        # Keep the scheduler fencing lease valid until *all* children finish.
        deadline = time.monotonic() + float(runtime.config.child_timeout_seconds) + 5.0
        while time.monotonic() < deadline:
            if runtime.goals.get(goal.id).state != "RUNNING":
                for cid in launched:
                    children.cancel(cid)
                raise RuntimeError("Automatic goal cancelled while child batch was running")
            if all(
                (child := children.repo.get(cid)) is not None
                and child.state in _TERMINAL
                for cid in launched
            ):
                break
            time.sleep(0.15)
        else:
            for cid in launched:
                child = children.repo.get(cid)
                if child is not None and child.state not in _TERMINAL:
                    children.cancel(cid)
            raise TimeoutError("Parallel automatic contract batch did not settle before lease release")

    child = children.repo.get(roster[current.local_id])
    if child is None:
        raise RuntimeError("Parallel contract child record disappeared")
    if child.state != "COMPLETED":
        return {
            "status": "INCOMPLETE" if child.state in {"FAILED", "TIMED_OUT"} else "BLOCKED",
            "error": f"Parallel child {current.local_id} {child.state}: {child.error or 'no completed result'}",
            "paths": [],
            "tokens": int(child.tokens_used or 0),
        }
    return {
        "status": "SUCCEEDED",
        "response": str(child.context_summary or "Child completed work for the assigned item."),
        "paths": list(current.paths),
        "tokens": int(child.tokens_used or 0),
    }


def forget_failed_parallel_item(state, goal_id: str, local_id: str) -> None:
    key = _roster_key(goal_id)
    roster = dict(state.get(key) or {})
    if roster.pop(local_id, None) is not None:
        state.set(key, roster, ttl_seconds=24 * 60 * 60)
        state.set(f"goal.parallel_failed:{goal_id}:{local_id}", True, ttl_seconds=24 * 60 * 60)
