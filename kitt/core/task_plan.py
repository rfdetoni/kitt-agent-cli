"""Bounded, host-owned task readiness and evidence over the existing EventLedger.

Plans are optional model proposals. They never grant permissions, execute arbitrary
check commands, or treat a child report as verification of the integrated workspace.
"""

from __future__ import annotations

import copy
import hashlib
import json
import threading
import uuid
from pathlib import Path

from kitt_protocol import AgentRole, HostExecutionState, SubagentReport
from kitt.security.workspace_fs import WorkspaceFileSystem
from kitt.validation.orchestrator import VerificationOrchestrator


class TaskPlanCoordinator:
    MAX_TASKS = 12
    MAX_BYTES = 32768

    def __init__(self, ledger, root, children=None, *, max_iterations=3):
        self.ledger = ledger
        self.root = Path(root).resolve()
        self.fs = WorkspaceFileSystem(root)
        self.children = children
        self.max_iterations = max(1, min(3, int(max_iterations)))
        self._lock = threading.RLock()

    def _events(self, conversation_id, turn_id):
        if self.ledger is None:
            raise RuntimeError("Task plans require a durable EventLedger")
        cursor = 0
        while True:
            batch = self.ledger.events(
                conversation_id, turn_id=turn_id, after_sequence=cursor, limit=1000
            )
            if not batch:
                return
            yield from batch
            cursor = batch[-1].sequence

    def inspect(self, conversation_id, turn_id):
        plan = None
        for event in self._events(conversation_id, turn_id):
            if event.event_type == "TaskPlanUpdated":
                plan = copy.deepcopy(event.payload)
        return plan

    def _save(self, conversation_id, turn_id, plan):
        plan["revision"] += 1
        self.ledger.append_event(
            conversation_id, "TaskPlanUpdated", plan, turn_id=turn_id, source="task-plan"
        )
        return copy.deepcopy(plan)

    @staticmethod
    def _strings(value, name, maximum=64):
        if (
            not isinstance(value, list)
            or len(value) > maximum
            or any(not isinstance(v, str) or not v.strip() or len(v) > 256 for v in value)
        ):
            raise ValueError(f"{name} must be a bounded list of non-empty strings")
        if len(set(value)) != len(value):
            raise ValueError(f"duplicate {name}")
        return list(value)

    def submit(self, conversation_id, turn_id, proposal, security_context):
        with self._lock:
            if self.inspect(conversation_id, turn_id) is not None:
                raise ValueError("A plan already exists for this turn; inspect and continue it")
            if (
                not isinstance(proposal, dict)
                or len(json.dumps(proposal).encode()) > self.MAX_BYTES
            ):
                raise ValueError("Plan proposal exceeds the bounded wire contract")
            if set(proposal) - {"schema_version", "objective", "tasks"}:
                raise ValueError("Unknown plan proposal field")
            if proposal.get("schema_version") != 1:
                raise ValueError("Unsupported plan schema version")
            objective = proposal.get("objective")
            if not isinstance(objective, str) or not objective.strip() or len(objective) > 2000:
                raise ValueError("Plan objective must contain 1 to 2000 characters")
            raw_tasks = proposal.get("tasks")
            if not isinstance(raw_tasks, list) or not 1 <= len(raw_tasks) <= self.MAX_TASKS:
                raise ValueError(f"Plan must contain 1 to {self.MAX_TASKS} tasks")
            tasks = []
            for raw in raw_tasks:
                if not isinstance(raw, dict) or set(raw) - {
                    "local_id",
                    "title",
                    "role",
                    "depends_on",
                    "check_ids",
                    "paths",
                }:
                    raise ValueError("Invalid task proposal fields")
                local_id, title = raw.get("local_id"), raw.get("title")
                if not isinstance(local_id, str) or not local_id or len(local_id) > 64:
                    raise ValueError("Invalid task local_id")
                if not isinstance(title, str) or not title.strip() or len(title) > 1000:
                    raise ValueError("Invalid task title")
                paths = self._strings(raw.get("paths", []), "paths")
                role = str(AgentRole(raw.get("role", "IMPLEMENT")))
                check_ids = self._strings(raw.get("check_ids", []), "check_ids", 24)
                if role in {"IMPLEMENT", "VERIFY"} and not paths and not check_ids:
                    raise ValueError("Implementation and verification tasks require concrete paths or registered checks")
                for path in paths:
                    self.fs.relative(path)
                    security_context.assert_path_allowed(path)
                    # Concrete paths make evidence freshness explicit and bounded.
                    if path == "." or self.fs.absolute_lexical(path).is_dir():
                        raise ValueError("Task paths must name concrete files")
                tasks.append(
                    {
                        "task_id": str(uuid.uuid4()),
                        "local_id": local_id,
                        "title": title,
                        "role": role,
                        "depends_on": self._strings(raw.get("depends_on", []), "depends_on", 12),
                        "check_ids": check_ids,
                        "paths": paths,
                        "status": "PENDING",
                        "attempts": 0,
                        "checks": {},
                        "child_id": None,
                        "verified_digest": None,
                    }
                )
            by_label = {t["local_id"]: t for t in tasks}
            if len({p for t in tasks for p in t["paths"]}) > 64:
                raise ValueError("Plan exceeds 64 concrete paths")
            if len({c for t in tasks for c in t["check_ids"]}) > 24:
                raise ValueError("Plan exceeds 24 distinct registered checks")
            if len(by_label) != len(tasks):
                raise ValueError("Duplicate task label")
            visited, active = set(), set()

            def visit(label):
                if label not in by_label:
                    raise ValueError("Unknown dependency")
                if label in active:
                    raise ValueError("Cyclic task dependencies")
                if label in visited:
                    return
                active.add(label)
                for dependency in by_label[label]["depends_on"]:
                    visit(dependency)
                active.remove(label)
                visited.add(label)

            for label in by_label:
                visit(label)
            plan = {
                "schema_version": 1,
                "plan_id": str(uuid.uuid4()),
                "objective": objective,
                "revision": 0,
                "tasks": tasks,
            }
            return self._save(conversation_id, turn_id, plan)

    def _digest(self, paths):
        entries = []
        total_bytes = 0
        for path in paths:
            try:
                content = self.fs.read(path)
                total_bytes += len(content.content)
                if total_bytes > 8 * 1024 * 1024:
                    raise ValueError("Verification scope exceeds 8 MiB; narrow concrete paths")
                digest = content.sha256
            except FileNotFoundError:
                digest = None
            entries.append((path, digest))
        return hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()

    def _task(self, plan, task_id):
        if not plan:
            raise ValueError("No task plan for this turn")
        for task in plan["tasks"]:
            if task["task_id"] == task_id:
                return task
        raise ValueError("Task does not belong to this turn")

    def next(self, conversation_id, turn_id):
        with self._lock:
            plan = self.inspect(conversation_id, turn_id)
            if not plan:
                return {"plan": None, "ready": []}
            changed = False
            for task in plan["tasks"]:
                if task["status"] == "VERIFIED" and task["verified_digest"] != self._digest(
                    task["paths"]
                ):
                    task.update(status="PENDING", checks={}, verified_digest=None)
                    changed = True
                if task["child_id"] and self.children is not None and task["status"] == "RUNNING":
                    child = self.children.repo.get(task["child_id"])
                    if child and child.state in {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"}:
                        task["status"] = "EXECUTED" if child.state == "COMPLETED" else "BLOCKED"
                        report = SubagentReport(
                            task["task_id"],
                            child.id,
                            child.state,
                            artifacts=(child.result_artifact_id,)
                            if child.result_artifact_id
                            else (),
                            blockers=(str(child.error),) if child.error else (),
                        )
                        self.ledger.append_event(
                            conversation_id,
                            "SubagentReport",
                            report.to_mapping(),
                            turn_id=turn_id,
                            source="task-plan",
                            event_id=f"task-report:{task['task_id']}:{child.id}",
                        )
                        changed = True
            # Invalid evidence also invalidates already-verified dependants.
            for _ in range(len(plan["tasks"])):
                valid = {t["local_id"] for t in plan["tasks"] if t["status"] == "VERIFIED"}
                stale = [
                    t
                    for t in plan["tasks"]
                    if t["status"] == "VERIFIED" and not set(t["depends_on"]) <= valid
                ]
                if not stale:
                    break
                for task in stale:
                    task.update(status="PENDING", checks={}, verified_digest=None)
                changed = True
            if changed:
                self._save(conversation_id, turn_id, plan)
            done = {t["local_id"] for t in plan["tasks"] if t["status"] == "VERIFIED"}
            ready = [
                t["task_id"]
                for t in plan["tasks"]
                if t["status"] in {"PENDING", "EXECUTED"} and set(t["depends_on"]) <= done
            ]
            return {"plan": plan, "ready": ready}

    def prepare_dispatch(self, conversation_id, turn_id, args, security_context):
        view = self.next(conversation_id, turn_id)
        task = self._task(view["plan"], args.get("task_id"))
        if task["task_id"] not in view["ready"] or task["status"] != "PENDING":
            raise ValueError("Task is not ready for delegation")
        if security_context.principal_type == "CHILD":
            raise PermissionError("Leaf workers cannot delegate")
        tools = self._strings(args.get("enabled_tools", ["read_file", "search"]), "enabled_tools")
        return {
            "name": task["local_id"],
            "task": task["title"],
            "allowed_paths": task["paths"],
            "enabled_tools": tools,
            "token_budget": args.get("token_budget", 2048),
            "timeout_seconds": args.get("timeout_seconds", 120),
            "plan_task_id": task["task_id"],
            "agent_role": task["role"],
        }

    def bind_child(self, conversation_id, turn_id, task_id, child_id):
        with self._lock:
            plan = self.inspect(conversation_id, turn_id)
            task = self._task(plan, task_id)
            child = self.children.repo.get(child_id) if self.children is not None else None
            if (
                child is None
                or child.parent_conversation_id != conversation_id
                or child.parent_turn_id != turn_id
            ):
                raise PermissionError("Child lineage does not match task ownership")
            if task["child_id"]:
                raise ValueError("Task already has a delegated attempt")
            task.update(child_id=child_id, status="RUNNING")
            self._save(conversation_id, turn_id, plan)

    def registered_check(self, conversation_id, turn_id, name, args):
        """Match exact host-registered argv, never command-name heuristics."""
        if name != "run_command" or args.get("cwd") not in {None, "", ".", str(self.root)}:
            return []
        plan = self.inspect(conversation_id, turn_id)
        bindings = []
        for task in (plan or {}).get("tasks", []):
            available = VerificationOrchestrator(self.root)._plan(task["paths"], True)
            for step in available:
                if step.name in task["check_ids"] and args.get("argv") == step.argv:
                    bindings.append((task["task_id"], step.name, self._digest(task["paths"])))
        return bindings

    def record_check(self, conversation_id, turn_id, bindings, result):
        if result.requires_approval or not bindings:
            return
        with self._lock:
            plan = self.inspect(conversation_id, turn_id)
            metadata = result.metadata or {}
            ok = bool(
                result.success
                and metadata.get("returncode") == 0
                and not metadata.get("timed_out")
                and not metadata.get("cancelled")
            )
            for task_id, check_id, digest in bindings:
                task = self._task(plan, task_id)
                task["checks"][check_id] = {
                    "status": "PASS" if ok else "FAIL",
                    "digest": digest,
                    "returncode": metadata.get("returncode"),
                }
            self._save(conversation_id, turn_id, plan)

    def verification_steps(self, conversation_id, turn_id, task_id, security_context):
        view = self.next(conversation_id, turn_id)
        task = self._task(view["plan"], task_id)
        if task_id not in view["ready"]:
            raise ValueError("Task dependencies or child execution are pending")
        if task["attempts"] >= self.max_iterations:
            raise ValueError("Task verification exhausted its configured attempts")
        for path in task["paths"]:
            security_context.assert_path_allowed(path)
        verifier = VerificationOrchestrator(self.root)
        available = {s.name: s for s in verifier._plan(task["paths"], True)}
        missing = set(task["check_ids"]) - available.keys()
        if missing:
            raise ValueError("Required checks unavailable: " + ", ".join(sorted(missing)))
        return task, [available[c] for c in task["check_ids"]], self._digest(task["paths"])

    def record_verification(self, conversation_id, turn_id, task_id, digest, checks, ok):
        with self._lock:
            plan = self.inspect(conversation_id, turn_id)
            task = self._task(plan, task_id)
            fresh = digest == self._digest(task["paths"])
            ok = ok and all(
                checks.get(c, {}).get("status") == "PASS" and checks[c].get("digest") == digest
                for c in task["check_ids"]
            )
            task["attempts"] += 1
            task["checks"] = checks
            task["status"] = (
                "VERIFIED"
                if ok and fresh
                else ("BLOCKED" if task["attempts"] >= self.max_iterations else "PENDING")
            )
            task["verified_digest"] = digest if ok and fresh else None
            return self._save(conversation_id, turn_id, plan)

    def observe_tool(
        self, conversation_id, turn_id, execution_id, name, args, result, mutating, discovery
    ):
        if self.ledger is None or getattr(result, "requires_approval", False):
            return
        if self.ledger.event_by_id(f"host-evidence:{execution_id}") is not None:
            return
        metadata = dict(getattr(result, "metadata", {}) or {})
        report = metadata.get("verification") or {}
        from kitt.core.agent_runtime import _file_mutation

        authoritative_verification = (
            _file_mutation(name, args) or args.get("operation") == "plan.verify"
        )
        verified = bool(
            authoritative_verification
            and report.get("ok")
            and report.get("status") in {"PASS", "NOT_APPLICABLE"}
        )
        inner = args.get("arguments", {}) if name == "kitt_runtime" else args
        paths = list(metadata.get("changed_paths") or [])
        if not paths and isinstance(inner, dict) and inner.get("path"):
            paths = [str(inner["path"])]
        self.ledger.append_event(
            conversation_id,
            "HostToolEvidence",
            {
                "execution_id": execution_id,
                "operation": args.get("operation", name),
                "success": bool(result.success),
                "mutating": bool(mutating),
                "discovery": bool(discovery and result.success),
                "verified": verified,
                "paths": paths,
                "verification_paths": list(report.get("checked_paths") or paths),
                "workspace_verified": bool(verified and report.get("workspace_verified")),
                "returncode": metadata.get("returncode"),
                "timed_out": bool(metadata.get("timed_out")),
                "cancelled": bool(metadata.get("cancelled")),
            },
            turn_id=turn_id,
            source="host-execution",
            event_id=f"host-evidence:{execution_id}",
        )
        if mutating and not metadata.get("post_edit_rolled_back"):
            with self._lock:
                plan = self.inspect(conversation_id, turn_id)
                if plan:
                    for task in plan["tasks"]:
                        if task["status"] == "VERIFIED":
                            task.update(status="PENDING", checks={}, verified_digest=None)
                    self._save(conversation_id, turn_id, plan)

    def host_state(self, conversation_id, turn_id):
        count = mutations = verified = 0
        discovery = validation = False
        pending_paths = set()
        opaque_effects = False
        for event in self._events(conversation_id, turn_id):
            if event.event_type != "HostToolEvidence":
                continue
            fact = event.payload
            count += 1
            discovery |= fact["discovery"]
            if fact["mutating"]:
                mutations += 1
                paths = fact.get("paths", [])
                pending_paths.update(paths)
                opaque_effects |= not bool(paths)
            if fact["verified"]:
                validation = True
                pending_paths.difference_update(fact.get("verification_paths", []))
                if fact.get("workspace_verified"):
                    pending_paths.clear()
                    opaque_effects = False
                if not pending_paths and not opaque_effects:
                    verified = mutations
        view = self.next(conversation_id, turn_id)
        complete = verified == mutations
        if view["plan"]:
            complete &= all(t["status"] == "VERIFIED" for t in view["plan"]["tasks"])
        if self.children is not None:
            complete &= not any(
                c.parent_turn_id == turn_id
                and c.state in {"CREATED", "QUEUED", "RUNNING", "WAITING_APPROVAL"}
                for c in self.children.repo.list(conversation_id, limit=100)
            )
        return HostExecutionState(
            conversation_id,
            turn_id,
            count,
            mutations,
            verified,
            discovery,
            validation,
            bool(complete),
        ).to_mapping()

    def verification_snapshot(self, conversation_id, turn_id, *, host_execution=None):
        """Project verification obligations from existing host-owned execution facts."""
        host = dict(host_execution or self.host_state(conversation_id, turn_id))
        plan = self.inspect(conversation_id, turn_id)
        obligations = []

        mutation_count = int(host.get("mutation_count") or 0)
        verified_mutations = int(host.get("verified_mutation_count") or 0)
        if mutation_count:
            mutation_status = "VERIFIED" if verified_mutations == mutation_count else "OPEN"
            obligations.append(
                {
                    "id": "mutations-verified",
                    "title": "Every workspace mutation has current host verification",
                    "status": mutation_status,
                    "authority": "host-execution",
                    "evidence": {
                        "mutation_count": mutation_count,
                        "verified_mutation_count": verified_mutations,
                    },
                    "required_next_evidence": (
                        None
                        if mutation_status == "VERIFIED"
                        else "Run a registered verification that covers every pending mutation path."
                    ),
                }
            )

        for task in (plan or {}).get("tasks", []):
            task_status = str(task.get("status") or "PENDING").upper()
            status = (
                "VERIFIED"
                if task_status == "VERIFIED"
                else "BLOCKED"
                if task_status == "BLOCKED"
                else "OPEN"
            )
            checks = task.get("checks") if isinstance(task.get("checks"), dict) else {}
            obligations.append(
                {
                    "id": f"task:{task.get('task_id')}",
                    "title": str(task.get("title") or task.get("local_id") or "Task")[:1000],
                    "status": status,
                    "authority": "task-plan",
                    "evidence": {
                        "task_status": task_status,
                        "attempts": int(task.get("attempts") or 0),
                        "check_statuses": {
                            str(name): str((value or {}).get("status") or "UNKNOWN")
                            for name, value in checks.items()
                            if isinstance(value, dict)
                        },
                        "verified_digest": task.get("verified_digest"),
                    },
                    "required_next_evidence": (
                        None
                        if status == "VERIFIED"
                        else "Verify the task against its current declared paths and registered checks."
                    ),
                }
            )

        active_children = []
        if self.children is not None:
            active_children = [
                child.id
                for child in self.children.repo.list(conversation_id, limit=100)
                if child.parent_turn_id == turn_id
                and child.state in {"CREATED", "QUEUED", "RUNNING", "WAITING_APPROVAL"}
            ]
        if active_children:
            obligations.append(
                {
                    "id": "children-settled",
                    "title": "All delegated work has reached a terminal state",
                    "status": "OPEN",
                    "authority": "child-lifecycle",
                    "evidence": {"active_child_count": len(active_children)},
                    "required_next_evidence": "Wait for or cancel active child work before completion.",
                }
            )

        if not obligations and not bool(host.get("completion_ready", False)):
            obligations.append(
                {
                    "id": "host-completion",
                    "title": "Host completion preconditions are satisfied",
                    "status": "OPEN",
                    "authority": "host-execution",
                    "evidence": {},
                    "required_next_evidence": "Resolve the remaining host-owned completion precondition.",
                }
            )

        unknowns = [
            {
                "id": f"unknown:{item['id']}",
                "obligation_id": item["id"],
                "reason": (
                    "required verification is blocked"
                    if item["status"] == "BLOCKED"
                    else "required verification has not been observed"
                ),
                "required_next_evidence": item.get("required_next_evidence"),
            }
            for item in obligations
            if item["status"] != "VERIFIED"
        ]
        status = (
            "BLOCKED"
            if any(item["status"] == "BLOCKED" for item in obligations)
            else "OPEN"
            if unknowns or not bool(host.get("completion_ready", False))
            else "READY"
        )
        return {
            "schema_version": 1,
            "status": status,
            "obligations": obligations,
            "residual_unknowns": unknowns,
        }

    def context(self, envelope, conversation_id, turn_id):
        if self.ledger is None:
            raise RuntimeError("Agent loop requires durable host evidence")
        result = copy.deepcopy(envelope or {"schema_version": 1, "epoch": turn_id, "segments": []})
        result["segments"] = [
            s for s in result["segments"] if s.get("id") != "host-execution-state"
        ]
        host_execution = self.host_state(conversation_id, turn_id)
        body = {
            "host_execution": host_execution,
            "task_plan": self.inspect(conversation_id, turn_id),
            "verification": self.verification_snapshot(
                conversation_id,
                turn_id,
                host_execution=host_execution,
            ),
        }
        result["segments"].append(
            {
                "id": "host-execution-state",
                "kind": "OUTPUT_CONTRACT",
                "source": "host-execution",
                "trust": "TRUSTED",
                "stability": "TURN",
                "priority": 100,
                "sensitivity": "PRIVATE",
                "recovery": "EXACT",
                "cache_region": "LIVE_ZONE",
                "lifecycle": "turn",
                "provenance_digest": hashlib.sha256(
                    json.dumps(body, sort_keys=True).encode()
                ).hexdigest(),
                "token_cost": len(json.dumps(body)) // 4 + 1,
                "body_ref": body,
            }
        )
        return result
