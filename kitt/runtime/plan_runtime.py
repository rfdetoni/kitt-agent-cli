"""Host-governed task-plan operations extracted from the runtime router.

The runtime facade remains the single authority for tool approvals, budgets,
scoped paths and execution. This module contains only the per-plan coordinator.
"""
from __future__ import annotations

from kitt.runtime.core_runtime import SafeRuntimeResult


def execute_plan_action(self, operation, args, turn_id, origin, security_context, grant, approval_id):
    plans = getattr(self.registry, "task_plans", None)
    if plans is None or security_context is None:
        raise RuntimeError("Durable task planning is unavailable")
    conv = self.conversation_id
    if operation == "plan.submit":
        data = plans.submit(conv, turn_id, args.get("proposal"), security_context)
    elif operation == "plan.inspect":
        data = plans.inspect(conv, turn_id)
    elif operation == "plan.patch":
        data = plans.patch(conv, turn_id, args.get("expected_revision"),
                           args.get("changes"), security_context)
    elif operation == "plan.next":
        data = plans.next(conv, turn_id)
    elif operation == "plan.checkpoint":
        data = plans.next(conv, turn_id)
        plans.ledger.append_event(conv, "TaskPlanCheckpoint", {
            "revision": (data["plan"] or {}).get("revision"), "ready": data["ready"],
            "verified": sum(t["status"] == "VERIFIED" for t in (data["plan"] or {}).get("tasks", [])),
        }, turn_id=turn_id, source="task-plan")
    elif operation == "plan.dispatch":
        payload = plans.prepare_dispatch(conv, turn_id, args, security_context)
        return self._op_registry_tool(operation, "child_spawn", payload, turn_id, origin,
                                      security_context, grant, approval_id)
    elif operation == "plan.dispatch_ready":
        # A single authenticated host action may admit multiple independent
        # children. Each spawn still passes normal policy and budget checks.
        if self.children is None:
            return SafeRuntimeResult(False, operation, error="Child manager not attached")
        view = plans.next(conv, turn_id)
        ready = set(view["ready"])
        tasks = (view["plan"] or {}).get("tasks", [])
        worker_limit = max(1, int(getattr(self.children, "max_children", 2)))
        requested_limit = args.get("max_parallel", worker_limit)
        count = min(worker_limit, max(1, int(requested_limit)))
        running = [t for t in tasks if t["status"] == "RUNNING"]
        occupied = {p for t in running for p in t["paths"]}
        free = max(0, count - len(running))
        started = []
        deferred = []
        for task in tasks:
            if task["task_id"] not in ready or task["status"] != "PENDING":
                continue
            paths = set(task["paths"])
            if not free or occupied.intersection(paths):
                deferred.append(task["task_id"])
                continue
            payload = plans.prepare_dispatch(
                conv, turn_id, {"task_id": task["task_id"], **{
                    key: args[key] for key in ("token_budget", "timeout_seconds", "enabled_tools")
                    if key in args
                }}, security_context
            )
            result = self._op_registry_tool(
                operation, "child_spawn", payload, turn_id, origin,
                security_context, grant, approval_id,
            )
            if result.requires_approval:
                # Return the normal approval payload; retrying dispatch_ready
                # after approval will skip any already admitted children.
                return result
            if not result.success:
                return SafeRuntimeResult(False, operation,
                    data={"started": started, "deferred": deferred},
                    error=result.error or "Subagent admission failed")
            started.extend(result.context_handles)
            occupied.update(paths)
            free -= 1
        return SafeRuntimeResult(True, operation,
            data={"started": started, "deferred": deferred,
                  "running": len(running) + len(started),
                  "requires_verification": True})
    else:
        task, steps, digest = plans.verification_steps(conv, turn_id, args.get("task_id"), security_context)
        checks = dict(task["checks"])
        for step in steps:
            if checks.get(step.name, {}).get("status") == "PASS" and checks[step.name].get("digest") == digest:
                continue
            result = self._op_registry_tool(operation, "run_command", {
                "argv": step.argv, "timeout_seconds": step.timeout_seconds,
            }, turn_id, origin, security_context, grant, approval_id)
            if result.requires_approval:
                return result
            checks[step.name] = {"status": "PASS" if result.success and result.metadata.get("returncode") == 0
                                 and not result.metadata.get("cancelled") and not result.metadata.get("timed_out") else "FAIL",
                                 "digest": digest, "returncode": result.metadata.get("returncode")}
            if checks[step.name]["status"] != "PASS":
                break
        # Syntax remains required even when no registered project check applies.
        from kitt.validation.post_edit import PostEditValidator
        syntax = PostEditValidator(self.root, getattr(self.registry, "process_runner", None)).validate_paths(task["paths"])
        ok = syntax.ok and all(checks.get(c, {}).get("status") == "PASS" for c in task["check_ids"])
        data = plans.record_verification(conv, turn_id, task["task_id"], digest, checks, ok)
        ok = next(t for t in data["tasks"] if t["task_id"] == task["task_id"])["status"] == "VERIFIED"
        return SafeRuntimeResult(ok, operation, data=data, error=None if ok else "Task verification failed",
                                 metadata={"verification": {"ok": ok, "status": "PASS" if ok else "FAIL",
                                     "checked_paths": task["paths"],
                                     "workspace_verified": bool(ok and any(step.scope == "workspace" for step in steps))}})
    return SafeRuntimeResult(True, operation, data=data)
