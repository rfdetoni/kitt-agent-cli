from __future__ import annotations

import json

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from kitt.context_engine.context_map import ContextMapBuilder
from kitt.domain.entities import FileSnapshot
from kitt.integrations.lsp import LanguageServerClient
from kitt.runtime.handles import ContextHandleResolver
from kitt.runtime.operation_registry import RuntimeOperationRegistry
from kitt.runtime.programmatic_flow import ProgrammaticToolFlow
from kitt.runtime.program_runtime import BoundedProgramRuntime
from kitt.runtime.persistent_program import PersistentProgramSessions
from kitt.runtime.progressive import apply_progressive_search_view
from kitt.runtime.retrieval_guard import RetrievalGuard
from kitt.runtime.state import RuntimeStateStore
from kitt.security.control_plane import control_plane_paths
from kitt.security.workspace_fs import DEFAULT_MAX_FILE_BYTES, WorkspaceFileSystem
from kitt.security.capabilities import (
    CAP_ARTIFACT_READ,
    CAP_ARTIFACT_WRITE,
    CAP_BROWSER_READ,
    CAP_BROWSER_WRITE,
    CAP_CHILD_INSPECT,
    CAP_CHILD_MESSAGE,
    CAP_CHILD_SPAWN,
    CAP_CONTROL_PLANE_WRITE,
    CAP_GOAL_MANAGE,
    CAP_MCP_CALL,
    CAP_MEMORY_READ,
    CAP_MEMORY_WRITE,
    CAP_NETWORK_ACCESS,
    CAP_PROCESS_RUN,
    CAP_REPO_READ,
    CAP_REPO_SEARCH,
    CAP_REPO_WRITE,
)


def _runtime_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _runtime_token_budget(args: dict, default: int = 1200) -> int:
    return _runtime_int(
        args.get("max_tokens", args.get("token_budget", default)),
        default,
        64,
        32_000,
    )


def _truncate_utf8(value: str, max_bytes: int) -> str:
    payload = value.encode("utf-8")
    if len(payload) <= max_bytes:
        return value
    keep = max(0, int(max_bytes))
    while keep > 0:
        try:
            return payload[:keep].decode("utf-8")
        except UnicodeDecodeError:
            keep -= 1
    return ""


def _bounded_symbol_payload(found: dict, max_tokens: int) -> dict:
    payload = dict(found)
    source = str(payload.get("source", "") or "")
    max_bytes = max_tokens * 4
    bounded = _truncate_utf8(source, max_bytes)
    payload["source"] = bounded
    payload["truncated"] = len(bounded.encode("utf-8")) < len(source.encode("utf-8"))
    payload["estimated_tokens"] = (len(bounded.encode("utf-8")) + 3) // 4
    return payload


@dataclass(frozen=True)
class RuntimeOperationSpec:
    name: str
    required_capability: Optional[str]
    policy_tool_action: Optional[str] = None
    sensitive: bool = False
    resume_tool_name: Optional[str] = None
    risk_cost: int = 0
    sandbox_profile: Optional[str] = None


OPERATION_REGISTRY = RuntimeOperationRegistry({
    "plan.submit": RuntimeOperationSpec("plan.submit", CAP_REPO_READ),
    "plan.inspect": RuntimeOperationSpec("plan.inspect", CAP_REPO_READ),
    "plan.next": RuntimeOperationSpec("plan.next", CAP_REPO_READ),
    "plan.checkpoint": RuntimeOperationSpec("plan.checkpoint", CAP_REPO_READ),
    "plan.dispatch": RuntimeOperationSpec("plan.dispatch", CAP_CHILD_SPAWN),
    "plan.dispatch_ready": RuntimeOperationSpec("plan.dispatch_ready", CAP_CHILD_SPAWN),
    "plan.verify": RuntimeOperationSpec("plan.verify", CAP_PROCESS_RUN),
    "repo.read": RuntimeOperationSpec("repo.read", CAP_REPO_READ, "read_file"),
    "repo.search": RuntimeOperationSpec("repo.search", CAP_REPO_SEARCH, "search"),
    "repo.inspect_symbol": RuntimeOperationSpec(
        "repo.inspect_symbol", CAP_REPO_READ, "read_file"
    ),
    "repo.read_symbol": RuntimeOperationSpec("repo.read_symbol", CAP_REPO_READ, "read_file"),
    "repo.references": RuntimeOperationSpec("repo.references", CAP_REPO_SEARCH, "search"),
    "repo.context_map": RuntimeOperationSpec(
        "repo.context_map", CAP_REPO_SEARCH, "search"
    ),
    "flow.execute": RuntimeOperationSpec("flow.execute", None, sensitive=False),
    "program.execute": RuntimeOperationSpec("program.execute", None, sensitive=False),
    "program.session.execute": RuntimeOperationSpec(
        "program.session.execute", CAP_REPO_READ, sensitive=False
    ),
    "program.session.get": RuntimeOperationSpec(
        "program.session.get", CAP_REPO_READ, sensitive=False
    ),
    "program.session.clear": RuntimeOperationSpec(
        "program.session.clear", CAP_REPO_READ, sensitive=False
    ),
    "repo.edit_symbol": RuntimeOperationSpec(
        "repo.edit_symbol", CAP_REPO_WRITE, "write_file", sensitive=True, risk_cost=1
    ),
    "artifacts.store": RuntimeOperationSpec(
        "artifacts.store",
        CAP_ARTIFACT_WRITE,
        "artifact_store",
        sensitive=True,
        resume_tool_name="artifact_store",
        risk_cost=1,
    ),
    "artifacts.read": RuntimeOperationSpec(
        "artifacts.read", CAP_ARTIFACT_READ, "artifact_read"
    ),
    "artifacts.search": RuntimeOperationSpec("artifacts.search", CAP_ARTIFACT_READ),
    "artifacts.hydrate": RuntimeOperationSpec("artifacts.hydrate", CAP_ARTIFACT_READ),
    "artifacts.retrieve": RuntimeOperationSpec("artifacts.retrieve", CAP_ARTIFACT_READ),
    "surface.capabilities": RuntimeOperationSpec("surface.capabilities", None),
    "surface.publish": RuntimeOperationSpec("surface.publish", None),
    "surface.patch": RuntimeOperationSpec("surface.patch", None),
    "surface.get": RuntimeOperationSpec("surface.get", None),
    "surface.delete": RuntimeOperationSpec("surface.delete", None),
    "surface.action": RuntimeOperationSpec("surface.action", None),
    "backend.validate": RuntimeOperationSpec("backend.validate", CAP_REPO_READ),
    "backend.plan": RuntimeOperationSpec("backend.plan", CAP_REPO_READ),
    "backend.compile": RuntimeOperationSpec("backend.compile", CAP_REPO_READ),
    "repo.create_directory": RuntimeOperationSpec(
        "repo.create_directory",
        CAP_REPO_WRITE,
        "create_directory",
        sensitive=True,
        resume_tool_name="create_directory",
        risk_cost=1,
    ),
    "patch.apply": RuntimeOperationSpec(
        "patch.apply",
        CAP_REPO_WRITE,
        "apply_patch",
        sensitive=True,
        resume_tool_name="apply_patch",
        risk_cost=1,
    ),
    "process.run": RuntimeOperationSpec(
        "process.run",
        CAP_PROCESS_RUN,
        "run_command",
        sensitive=True,
        resume_tool_name="run_command",
        risk_cost=3,
        sandbox_profile="workspace-write",
    ),
    "process.start": RuntimeOperationSpec(
        "process.start",
        CAP_PROCESS_RUN,
        "run_command",
        sensitive=True,
        risk_cost=3,
        sandbox_profile="workspace-write",
    ),
    "process.read": RuntimeOperationSpec(
        "process.read",
        CAP_PROCESS_RUN,
        sensitive=False,
    ),
    "process.stdin": RuntimeOperationSpec(
        "process.stdin",
        CAP_PROCESS_RUN,
        sensitive=True,
    ),
    "process.signal": RuntimeOperationSpec(
        "process.signal",
        CAP_PROCESS_RUN,
        sensitive=True,
    ),
    "process.stop": RuntimeOperationSpec(
        "process.stop",
        CAP_PROCESS_RUN,
        sensitive=True,
    ),
    "process.resume": RuntimeOperationSpec(
        "process.resume",
        CAP_PROCESS_RUN,
        sensitive=True,
    ),
    "children.spawn": RuntimeOperationSpec(
        "children.spawn",
        CAP_CHILD_SPAWN,
        "child_spawn",
        sensitive=True,
        resume_tool_name="child_spawn",
        risk_cost=2,
    ),
    "children.send": RuntimeOperationSpec(
        "children.send", CAP_CHILD_MESSAGE, sensitive=False
    ),
    "children.inspect": RuntimeOperationSpec(
        "children.inspect", CAP_CHILD_INSPECT, sensitive=False
    ),
    "children.observe": RuntimeOperationSpec(
        "children.observe", CAP_CHILD_INSPECT, sensitive=False
    ),
    "children.passivate": RuntimeOperationSpec(
        "children.passivate", CAP_CHILD_MESSAGE, sensitive=False
    ),
    "children.revive": RuntimeOperationSpec(
        "children.revive", CAP_CHILD_SPAWN, sensitive=False
    ),
    "goal.inspect": RuntimeOperationSpec(
        "goal.inspect", CAP_GOAL_MANAGE, sensitive=False
    ),
    "goal.update": RuntimeOperationSpec(
        "goal.update", CAP_GOAL_MANAGE, "goal_update", sensitive=True
    ),
    "schedule.create": RuntimeOperationSpec(
        "schedule.create", CAP_GOAL_MANAGE, "goal_update", sensitive=True
    ),
    "schedule.list": RuntimeOperationSpec(
        "schedule.list", CAP_GOAL_MANAGE, sensitive=False
    ),
    "schedule.cancel": RuntimeOperationSpec(
        "schedule.cancel", CAP_GOAL_MANAGE, "goal_update", sensitive=True
    ),
    "heartbeat.set": RuntimeOperationSpec(
        "heartbeat.set", CAP_GOAL_MANAGE, "goal_update", sensitive=True
    ),
    "memory.query": RuntimeOperationSpec(
        "memory.query", CAP_MEMORY_READ, sensitive=False
    ),
    "session.search": RuntimeOperationSpec(
        "session.search", CAP_MEMORY_READ, sensitive=False
    ),
    "memory.correct": RuntimeOperationSpec("memory.correct", CAP_MEMORY_WRITE, "memory_save", sensitive=True),
    "memory.concept": RuntimeOperationSpec("memory.concept", CAP_MEMORY_WRITE, "memory_save", sensitive=True),
    "memory.link": RuntimeOperationSpec("memory.link", CAP_MEMORY_WRITE, "memory_save", sensitive=True),
    "harness.refine.prepare": RuntimeOperationSpec(
        "harness.refine.prepare", CAP_MEMORY_WRITE, "memory_save", sensitive=True
    ),
    "harness.refine.apply": RuntimeOperationSpec(
        "harness.refine.apply", CAP_MEMORY_WRITE, "memory_save", sensitive=True
    ),
    "harness.refine.rollback": RuntimeOperationSpec(
        "harness.refine.rollback", CAP_MEMORY_WRITE, "memory_save", sensitive=True
    ),
    "skill.call": RuntimeOperationSpec("skill.call", CAP_REPO_READ, sensitive=False),
    "mcp.call": RuntimeOperationSpec(
        "mcp.call", CAP_MCP_CALL, "mcp_call", sensitive=True
    ),
    "browser.open": RuntimeOperationSpec("browser.open", CAP_BROWSER_READ),
    "browser.inspect": RuntimeOperationSpec("browser.inspect", CAP_BROWSER_READ),
    "browser.screenshot": RuntimeOperationSpec("browser.screenshot", CAP_BROWSER_READ),
    "browser.click": RuntimeOperationSpec(
        "browser.click", CAP_BROWSER_WRITE, "browser.click", sensitive=True, risk_cost=1
    ),
    "browser.type": RuntimeOperationSpec(
        "browser.type", CAP_BROWSER_WRITE, "browser.type", sensitive=True, risk_cost=1
    ),
    "browser.close": RuntimeOperationSpec(
        "browser.close", CAP_BROWSER_WRITE, "browser.close", sensitive=True, risk_cost=1
    ),
    "state.get": RuntimeOperationSpec("state.get", CAP_REPO_READ, sensitive=False),
    "state.set": RuntimeOperationSpec("state.set", CAP_REPO_WRITE, sensitive=False),
    "state.list": RuntimeOperationSpec("state.list", CAP_REPO_READ, sensitive=False),
    "handles.resolve": RuntimeOperationSpec(
        "handles.resolve", None, sensitive=False
    ),
    "repo.definition": RuntimeOperationSpec("repo.definition", CAP_REPO_READ, "read_file"),
    "repo.hover": RuntimeOperationSpec("repo.hover", CAP_REPO_READ, "read_file"),
    "repo.references_semantic": RuntimeOperationSpec(
        "repo.references_semantic", CAP_REPO_SEARCH, "search"
    ),
    "repo.diagnostics": RuntimeOperationSpec(
        "repo.diagnostics", CAP_REPO_READ, "read_file"
    ),
    "repo.call_hierarchy": RuntimeOperationSpec(
        "repo.call_hierarchy", CAP_REPO_SEARCH, "search"
    ),
    "repo.outline": RuntimeOperationSpec("repo.outline", CAP_REPO_READ, "read_file"),
    "repo.list": RuntimeOperationSpec("repo.list", CAP_REPO_READ, "list_files"),
    "repo.write_file": RuntimeOperationSpec(
        "repo.write_file",
        CAP_REPO_WRITE,
        "write_file",
        sensitive=True,
        resume_tool_name="write_file",
        risk_cost=1,
    ),
    "repo.move": RuntimeOperationSpec(
        "repo.move", CAP_REPO_WRITE, "write_file", sensitive=True, risk_cost=1
    ),
    "repo.rename": RuntimeOperationSpec(
        "repo.rename", CAP_REPO_WRITE, "write_file", sensitive=True, risk_cost=1
    ),
    "repo.delete": RuntimeOperationSpec(
        "repo.delete", CAP_REPO_WRITE, "write_file", sensitive=True, risk_cost=4
    ),
    "security.scan": RuntimeOperationSpec("security.scan", CAP_REPO_SEARCH, "search"),
})
OPERATION_SPECS = OPERATION_REGISTRY


@dataclass
class SafeRuntimeResult:
    success: bool
    operation: str
    data: Any = None
    error: Optional[str] = None
    context_handles: List[str] = field(default_factory=list)
    tokens_saved: int = 0
    duration_ms: float = 0.0
    requires_approval: bool = False
    approval_action: Optional[str] = None
    approval_payload: Optional[Dict[str, Any]] = None
    required_capability: Optional[str] = None
    resume_tool_name: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class SafeRuntime:
    """Compact policy-governed runtime that preserves the principal context."""

    operation_registry = OPERATION_REGISTRY

    def __init__(
        self,
        workspace_root: str | Path,
        workspace_id: str,
        conversation_id: str,
        tool_registry=None,
        repository_index=None,
        artifact_store=None,
        child_manager=None,
        goal_service=None,
        memory_service=None,
        skill_manager=None,
        surface_service=None,
        backend_service=None,
        process_manager=None,
        wake_scheduler=None,
        harness_refiner=None,
        state_store: Optional[RuntimeStateStore] = None,
        db=None,
    ):
        self.root = Path(workspace_root).resolve()
        self.workspace_id = workspace_id
        self.conversation_id = conversation_id
        self.registry = tool_registry
        self.index = repository_index
        self.artifacts = artifact_store
        self.children = child_manager
        self.goals = goal_service
        self.memory = memory_service
        self.skills = skill_manager
        self.surfaces = surface_service
        self.backend = backend_service
        self.process_manager = process_manager
        self.scheduler = wake_scheduler
        self.refiner = harness_refiner
        self.db = db
        self.state = state_store or (
            RuntimeStateStore(db, workspace_id, conversation_id) if db else None
        )
        self.handles = ContextHandleResolver(
            self.root,
            repository_index=self.index,
            artifact_store=self.artifacts,
            child_manager=self.children,
            goal_service=self.goals,
            workspace_id=self.workspace_id,
            conversation_id=self.conversation_id,
        )
        self.retrieval_guard = RetrievalGuard()
        self.programmatic_flow = ProgrammaticToolFlow(self)
        self.program_runtime = BoundedProgramRuntime(self)
        self.program_sessions = PersistentProgramSessions(self.state, self.program_runtime)
        self.context_map_builder = ContextMapBuilder(
            self.index, self.goals, self.registry
        )

    def _requested_control_plane_paths(
        self,
        operation: str,
        args: Dict[str, Any],
    ) -> tuple[str, ...]:
        candidates: list[object] = []
        if operation in {"repo.write_file", "repo.create_directory", "repo.delete"}:
            candidates.append(args.get("path") or args.get("file"))
        elif operation in {"repo.move", "repo.rename"}:
            candidates.extend(
                [
                    args.get("source") or args.get("path"),
                    args.get("destination") or args.get("target"),
                ]
            )
        elif operation == "repo.edit_symbol":
            candidates.append(args.get("path"))
        elif operation == "patch.apply" and self.registry is not None:
            parser = getattr(self.registry, "parser", None)
            if parser is not None:
                try:
                    blocks = parser.parse(str(args.get("patch", "") or ""))
                except Exception:
                    blocks = []
                candidates.extend(block.file_path for block in blocks)
        return control_plane_paths(candidates)

    def execute(
        self,
        operation: str,
        arguments: Optional[Dict[str, Any]] = None,
        turn_id: str = "runtime_turn",
        origin: str = "MODEL",
        effective_capabilities: Optional[Set[str]] = None,
        security_context: Optional[Any] = None,
        approval_grant: Optional[Any] = None,
        expected_approval_id: Optional[str] = None,
    ) -> SafeRuntimeResult:
        start = time.perf_counter()
        args = arguments or {}
        op = operation.strip() if operation else ""
        spec = self.operation_registry.get(op)
        if spec is None:
            return self._result(
                start,
                SafeRuntimeResult(False, op, error=f"Unknown runtime operation: '{op}'"),
            )

        if op == "handles.resolve" and security_context is None:
            return self._result(
                start,
                SafeRuntimeResult(
                    False,
                    op,
                    error="ExecutionSecurityContext is required for handles.resolve (fail-closed)",
                ),
            )

        if security_context is not None:
            try:
                security_context.assert_scope(self.workspace_id, self.conversation_id)
            except PermissionError as exc:
                return self._result(
                    start, SafeRuntimeResult(False, op, error=str(exc))
                )

        if security_context is not None and hasattr(security_context, "capabilities"):
            capabilities = set(security_context.capabilities)
        elif effective_capabilities is not None:
            capabilities = set(effective_capabilities)
        else:
            capabilities = set()

        if spec.required_capability and spec.required_capability not in capabilities:
            return self._result(
                start,
                SafeRuntimeResult(
                    False,
                    op,
                    error=(
                        f"Capability '{spec.required_capability}' required for '{op}' "
                        "is not granted (fail-closed)"
                    ),
                ),
            )

        if op == "repo.edit_symbol":
            try:
                args = self._canonicalize_repo_edit_args(args, security_context)
            except (KeyError, ValueError, RuntimeError, PermissionError) as exc:
                return self._result(
                    start, SafeRuntimeResult(False, op, error=f"Structural edit preflight failed: {exc}")
                )

        # A grant returned after a delegated tool requested approval must follow
        # the resume path back to that exact tool. The nested ToolRegistry remains
        # the authority that validates/consumes the grant against tool+args+scope.
        delegated_grant = (
            approval_grant
            if approval_grant is not None and spec.resume_tool_name
            else None
        )
        delegated_approval_id = (
            expected_approval_id if delegated_grant is not None else None
        )
        requested_control_paths = self._requested_control_plane_paths(op, args)
        control_plane_elevation = bool(
            requested_control_paths
            and CAP_CONTROL_PLANE_WRITE not in capabilities
        )
        network_elevation = bool(
            op in {"process.run", "process.start"}
            and args.get("network", False) is True
            and CAP_NETWORK_ACCESS not in capabilities
        )
        automatic_budget_reservation = None
        automatic_budget_reserved = False
        auto_review = None
        policy = (
            getattr(self.registry, "policy", None)
            if self.registry is not None
            else None
        )
        if control_plane_elevation and approval_grant is None:
            return self._result(
                start,
                SafeRuntimeResult(
                    False,
                    op,
                    error=(
                        f"Operation '{op}' targets KITT control-plane path(s) and "
                        "requires explicit user approval or control_plane.write."
                    ),
                    requires_approval=True,
                    approval_action=spec.policy_tool_action or op,
                    approval_payload=dict(args),
                    required_capability=CAP_CONTROL_PLANE_WRITE,
                    resume_tool_name=spec.resume_tool_name,
                    metadata={
                        "control_plane": {
                            "paths": list(requested_control_paths),
                            "required_capability": CAP_CONTROL_PLANE_WRITE,
                            "reason": "single-use control-plane elevation required",
                        }
                    },
                ),
            )

        if network_elevation and approval_grant is None:
            return self._result(
                start,
                SafeRuntimeResult(
                    False,
                    op,
                    error=(
                        f"Operation '{op}' requests network access and requires "
                        "explicit user approval or CAP_NETWORK_ACCESS."
                    ),
                    requires_approval=True,
                    approval_action=spec.policy_tool_action or op,
                    approval_payload=dict(args),
                    required_capability=CAP_NETWORK_ACCESS,
                    resume_tool_name=spec.resume_tool_name,
                    metadata={
                        "network": {
                            "requested": True,
                            "required_capability": CAP_NETWORK_ACCESS,
                            "reason": "single-use network elevation required",
                        }
                    },
                ),
            )

        if network_elevation and approval_grant is not None:
            delegated_grant = approval_grant
            delegated_approval_id = expected_approval_id
        if (
            control_plane_elevation
            and approval_grant is not None
            and spec.resume_tool_name
        ):
            delegated_grant = approval_grant
            delegated_approval_id = expected_approval_id
        if control_plane_elevation and approval_grant is not None and policy is None:
            return self._result(
                start,
                SafeRuntimeResult(
                    False,
                    op,
                    error=(
                        "Control-plane approval cannot be validated without "
                        "an attached policy/approval broker."
                    ),
                    required_capability=CAP_CONTROL_PLANE_WRITE,
                ),
            )

        if policy is not None:
            if (
                getattr(getattr(policy, "autonomy", None), "level", None) == "read_only"
                and (spec.sensitive or spec.risk_cost > 0)
            ):
                return self._result(
                    start,
                    SafeRuntimeResult(
                        False,
                        op,
                        error=f"Operation '{op}' is blocked in read_only autonomy mode",
                    ),
                )

            if spec.policy_tool_action:
                permission = policy.evaluate_tool(
                    spec.policy_tool_action, args, origin=origin
                )
                if control_plane_elevation and permission != "DENY":
                    permission = "ASK"
                if permission == "DENY":
                    return self._result(
                        start,
                        SafeRuntimeResult(
                            False,
                            op,
                            error=(
                                "Execution denied by PolicyEngine for tool "
                                f"'{spec.policy_tool_action}'."
                            ),
                        ),
                    )
                if permission == "ASK" and approval_grant is None:
                    review_profile = (
                        "workspace-write+network"
                        if bool(args.get("network", False))
                        else "workspace-write"
                    )
                    sandbox = getattr(
                        getattr(self.registry, "process_runner", None),
                        "sandbox",
                        None,
                    )
                    sandbox_strong = bool(
                        spec.policy_tool_action == "run_command"
                        and sandbox is not None
                        and sandbox.is_strong_available(review_profile)
                    )
                    auto_review = policy.review_ask_action(
                        spec.policy_tool_action,
                        args,
                        permission=permission,
                        origin=origin,
                        sandbox_strong=sandbox_strong,
                        network_requested=bool(args.get("network", False)),
                        control_plane_elevation=control_plane_elevation,
                    )
                    if auto_review.decision == "DENY":
                        return self._result(
                            start,
                            SafeRuntimeResult(
                                False,
                                op,
                                error=(
                                    f"Operation '{op}' was denied by pre-execution review."
                                ),
                                metadata={"auto_review": auto_review.to_dict()},
                            ),
                        )
                    if auto_review.allowed:
                        permission = "ALLOW"

                if permission == "ASK":
                    if approval_grant is None:
                        return self._result(
                            start,
                            SafeRuntimeResult(
                                False,
                                op,
                                error=f"Operation '{op}' requires approval from user.",
                                requires_approval=True,
                                approval_action=spec.policy_tool_action,
                                approval_payload=dict(args),
                                required_capability=spec.required_capability,
                                resume_tool_name=spec.resume_tool_name,
                                metadata=(
                                    {"auto_review": auto_review.to_dict()}
                                    if auto_review is not None
                                    else {}
                                ),
                            ),
                        )
                    if spec.resume_tool_name:
                        delegated_grant = approval_grant
                        delegated_approval_id = expected_approval_id
                    else:
                        action_hash = policy.generate_action_hash(
                            spec.policy_tool_action, args
                        )
                        valid = (
                            policy.approval_manager
                            and policy.approval_manager.validate_and_consume(
                                approval_grant,
                                action_hash,
                                turn_id,
                                self.conversation_id,
                                self.workspace_id,
                                expected_approval_id=expected_approval_id,
                            )
                        )
                        if not valid:
                            return self._result(
                                start,
                                SafeRuntimeResult(
                                    False,
                                    op,
                                    error=(
                                        "Approval grant is invalid, expired, mismatched, "
                                        "or already consumed."
                                    ),
                                ),
                            )

        if (
            policy is not None
            and approval_grant is None
            and spec.risk_cost > 0
        ):
            automatic_budget_reservation = policy.reserve_automatic_action(
                spec.policy_tool_action or op,
                turn_id=turn_id,
                conversation_id=self.conversation_id,
                origin=origin,
                risk_cost=spec.risk_cost,
            )
            if not automatic_budget_reservation.allowed:
                return self._result(
                    start,
                    SafeRuntimeResult(
                        False,
                        op,
                        error=(
                            f"Operation '{op}' exceeded the automatic risk budget "
                            "and requires explicit user approval."
                        ),
                        requires_approval=True,
                        approval_action=spec.policy_tool_action or op,
                        approval_payload=dict(args),
                        required_capability=spec.required_capability,
                        resume_tool_name=spec.resume_tool_name,
                        metadata={
                            "risk_budget": automatic_budget_reservation.to_dict(),
                            **(
                                {"auto_review": auto_review.to_dict()}
                                if auto_review is not None
                                else {}
                            ),
                        },
                    ),
                )
            automatic_budget_reserved = automatic_budget_reservation.reserved

        try:
            result = self._dispatch(
                op,
                args,
                turn_id,
                origin,
                security_context,
                capabilities,
                delegated_grant,
                delegated_approval_id,
                automatic_budget_reserved,
            )
            if op == "repo.search":
                result = apply_progressive_search_view(result, args)
            if op in {"repo.read", "repo.search"}:
                result = self.retrieval_guard.observe(op, args, result)
            elif result.success and op in {"repo.edit_symbol", "repo.create_directory", "patch.apply"}:
                self.retrieval_guard.invalidate()
            if auto_review is not None:
                result.metadata = {
                    **dict(result.metadata or {}),
                    "auto_review": auto_review.to_dict(),
                }
            if automatic_budget_reservation is not None and automatic_budget_reservation.reserved:
                result.metadata = {
                    **dict(result.metadata or {}),
                    "risk_budget": automatic_budget_reservation.to_dict(),
                }
        except Exception as exc:
            result = SafeRuntimeResult(
                False, op, error=f"Runtime error in {op}: {exc}"
            )
        return self._result(start, result)

    @staticmethod
    def _result(start: float, result: SafeRuntimeResult) -> SafeRuntimeResult:
        result.duration_ms = (time.perf_counter() - start) * 1000
        return result

    def _dispatch(
        self,
        op: str,
        args: dict,
        turn_id: str,
        origin: str,
        security_context,
        capabilities: set[str],
        grant,
        expected_approval_id,
        automatic_budget_reserved: bool = False,
    ) -> SafeRuntimeResult:
        handlers = {
            "repo.read": lambda: self._op_repo_read(args, turn_id, origin, security_context),
            "repo.search": lambda: self._op_repo_search(args, turn_id, origin, security_context),
            "repo.inspect_symbol": lambda: self._op_repo_inspect_symbol(args, turn_id, origin, security_context),
            "repo.read_symbol": lambda: self._op_repo_read_symbol(args, security_context),
            "repo.references": lambda: self._op_repo_references(args, security_context),
            "repo.definition": lambda: self._op_repo_lsp(
                "repo.definition", "textDocument/definition", args, security_context
            ),
            "repo.hover": lambda: self._op_repo_lsp(
                "repo.hover", "textDocument/hover", args, security_context
            ),
            "repo.references_semantic": lambda: self._op_repo_lsp(
                "repo.references_semantic", "textDocument/references", args, security_context
            ),
            "repo.diagnostics": lambda: self._op_repo_lsp(
                "repo.diagnostics", "textDocument/diagnostic", args, security_context
            ),
            "repo.outline": lambda: self._op_repo_lsp(
                "repo.outline", "textDocument/documentSymbol", args, security_context
            ),
            "repo.call_hierarchy": lambda: self._op_repo_call_hierarchy(
                args, security_context
            ),
            "repo.context_map": lambda: self._op_repo_context_map(
                args, security_context
            ),
            "flow.execute": lambda: self._op_flow_execute(
                args, turn_id, origin, capabilities, security_context
            ),
            "program.execute": lambda: self._op_program_execute(
                args, turn_id, origin, capabilities, security_context
            ),
            "program.session.execute": lambda: self._op_program_session_execute(
                args, turn_id, origin, capabilities, security_context
            ),
            "program.session.get": lambda: self._op_program_session_get(args),
            "program.session.clear": lambda: self._op_program_session_clear(args),
            "repo.edit_symbol": lambda: self._op_repo_edit_symbol(args, turn_id, security_context),
            "artifacts.store": lambda: self._op_registry_tool("artifacts.store", "artifact_store", args, turn_id, origin, security_context, grant, expected_approval_id, automatic_budget_reserved),
            "artifacts.read": lambda: self._op_registry_tool("artifacts.read", "artifact_read", args, turn_id, origin, security_context, automatic_budget_reserved=automatic_budget_reserved),
            "artifacts.search": lambda: self._op_artifacts_search(args),
            "artifacts.hydrate": lambda: self._op_artifacts_hydrate(args),
            "artifacts.retrieve": lambda: self._op_artifacts_retrieve(args),
            "surface.capabilities": lambda: self._op_surface_capabilities(),
            "surface.publish": lambda: self._op_surface_publish(args),
            "surface.patch": lambda: self._op_surface_patch(args),
            "surface.get": lambda: self._op_surface_get(args),
            "surface.delete": lambda: self._op_surface_delete(args),
            "surface.action": lambda: self._op_surface_action(args),
            "backend.validate": lambda: self._op_backend_validate(args),
            "backend.plan": lambda: self._op_backend_plan(args),
            "backend.compile": lambda: self._op_backend_compile(args),
            "repo.create_directory": lambda: self._op_registry_tool("repo.create_directory", "create_directory", args, turn_id, origin, security_context, grant, expected_approval_id, automatic_budget_reserved),
            "patch.apply": lambda: self._op_registry_tool("patch.apply", "apply_patch", args, turn_id, origin, security_context, grant, expected_approval_id, automatic_budget_reserved),
            "process.run": lambda: self._op_registry_tool("process.run", "run_command", args, turn_id, origin, security_context, grant, expected_approval_id, automatic_budget_reserved),
            "process.start": lambda: self._op_process_start(
                args, turn_id, security_context
            ),
            "process.read": lambda: self._op_process_read(
                args, security_context
            ),
            "process.stdin": lambda: self._op_process_stdin(
                args, turn_id, security_context
            ),
            "process.signal": lambda: self._op_process_signal(
                args, turn_id, security_context
            ),
            "process.stop": lambda: self._op_process_stop(
                args, turn_id, security_context
            ),
            "process.resume": lambda: self._op_process_resume(
                args, turn_id, security_context
            ),
            "children.spawn": lambda: self._op_registry_tool("children.spawn", "child_spawn", args, turn_id, origin, security_context, grant, expected_approval_id, automatic_budget_reserved),
            "children.send": lambda: self._op_children_send(args),
            "children.inspect": lambda: self._op_children_inspect(args),
            "children.observe": lambda: self._op_children_observe(),
            "children.passivate": lambda: self._op_children_passivate(args),
            "children.revive": lambda: self._op_children_revive(args),
            "goal.inspect": lambda: self._op_goal_inspect(args),
            "goal.update": lambda: self._op_goal_update(args),
            "schedule.create": lambda: self._op_schedule_create(args),
            "schedule.list": lambda: self._op_schedule_list(args),
            "schedule.cancel": lambda: self._op_schedule_cancel(args),
            "heartbeat.set": lambda: self._op_heartbeat_set(args),
            "memory.query": lambda: self._op_memory_query(args),
            "session.search": lambda: self._op_session_search(args),
            "memory.correct": lambda: self._op_memory_correct(args),
            "memory.concept": lambda: self._op_memory_concept(args),
            "memory.link": lambda: self._op_memory_link(args),
            "harness.refine.prepare": lambda: self._op_harness_refine_prepare(args),
            "harness.refine.apply": lambda: self._op_harness_refine_apply(args),
            "harness.refine.rollback": lambda: self._op_harness_refine_rollback(args),
            "skill.call": lambda: self._op_skill_call(args, security_context),
            "mcp.call": lambda: self._op_mcp_call(
                args, turn_id, security_context, automatic_budget_reserved
            ),
            "browser.open": lambda: self._op_browser_action("browser.open", args),
            "browser.inspect": lambda: self._op_browser_action("browser.inspect", args),
            "browser.screenshot": lambda: self._op_browser_action("browser.screenshot", args),
            "browser.click": lambda: self._op_browser_action("browser.click", args),
            "browser.type": lambda: self._op_browser_action("browser.type", args),
            "browser.close": lambda: self._op_browser_action("browser.close", args),
            "state.get": lambda: self._op_state_get(args),
            "state.set": lambda: self._op_state_set(args),
            "state.list": lambda: self._op_state_list(),
            "handles.resolve": lambda: self._op_handles_resolve(args, security_context),
        }
        if op.startswith("plan."):
            return self._op_plan(op, args, turn_id, origin, security_context, grant, expected_approval_id)
        return handlers[op]()

    def _op_plan(self, operation, args, turn_id, origin, security_context, grant, approval_id):
        plans = getattr(self.registry, "task_plans", None)
        if plans is None or security_context is None:
            raise RuntimeError("Durable task planning is unavailable")
        conv = self.conversation_id
        if operation == "plan.submit":
            data = plans.submit(conv, turn_id, args.get("proposal"), security_context)
        elif operation == "plan.inspect":
            data = plans.inspect(conv, turn_id)
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

    def _managed_processes(self):
        manager = self.process_manager
        if manager is None and self.registry is not None:
            manager = getattr(self.registry, "process_manager", None)
        if manager is None:
            raise RuntimeError("managed process lifecycle is unavailable")
        return manager

    def _op_process_start(
        self,
        args: dict,
        turn_id: str,
        security_context,
    ) -> SafeRuntimeResult:
        argv = args.get("argv")
        if not isinstance(argv, list):
            return SafeRuntimeResult(
                False,
                "process.start",
                error="process.start requires argv as a string list",
            )
        profile = (
            "workspace-write+network"
            if args.get("network") is True
            else str(args.get("sandbox_profile") or "workspace-write")
        )
        data = self._managed_processes().start(
            argv=argv,
            conversation_id=self.conversation_id,
            turn_id=turn_id,
            security_context=security_context,
            cwd=args.get("cwd"),
            env=args.get("env") if isinstance(args.get("env"), dict) else None,
            sandbox_profile=profile,
            require_strong_sandbox=bool(args.get("require_strong_sandbox", False)),
        )
        return SafeRuntimeResult(True, "process.start", data=data)

    def _op_process_read(
        self,
        args: dict,
        security_context,
    ) -> SafeRuntimeResult:
        data = self._managed_processes().read(
            str(args.get("process_id") or ""),
            security_context=security_context,
            after_seq=int(args.get("after_seq", 0) or 0),
            limit=int(args.get("limit", 100) or 100),
        )
        return SafeRuntimeResult(True, "process.read", data=data)

    def _op_process_stdin(
        self,
        args: dict,
        turn_id: str,
        security_context,
    ) -> SafeRuntimeResult:
        data = self._managed_processes().stdin(
            str(args.get("process_id") or ""),
            str(args.get("data") or ""),
            security_context=security_context,
            turn_id=turn_id,
        )
        return SafeRuntimeResult(True, "process.stdin", data=data)

    def _op_process_signal(
        self,
        args: dict,
        turn_id: str,
        security_context,
    ) -> SafeRuntimeResult:
        data = self._managed_processes().signal(
            str(args.get("process_id") or ""),
            str(args.get("signal") or ""),
            security_context=security_context,
            turn_id=turn_id,
        )
        return SafeRuntimeResult(True, "process.signal", data=data)

    def _op_process_stop(
        self,
        args: dict,
        turn_id: str,
        security_context,
    ) -> SafeRuntimeResult:
        data = self._managed_processes().stop(
            str(args.get("process_id") or ""),
            security_context=security_context,
            turn_id=turn_id,
        )
        return SafeRuntimeResult(True, "process.stop", data=data)

    def _op_process_resume(
        self,
        args: dict,
        turn_id: str,
        security_context,
    ) -> SafeRuntimeResult:
        data = self._managed_processes().resume(
            str(args.get("process_id") or ""),
            security_context=security_context,
            turn_id=turn_id,
        )
        return SafeRuntimeResult(True, "process.resume", data=data)

    def _op_browser_action(self, operation: str, args: dict) -> SafeRuntimeResult:
        if not self.registry or not hasattr(self.registry, "get_browser_gateway"):
            return SafeRuntimeResult(False, operation, error="Browser gateway is unavailable")
        gateway = self.registry.get_browser_gateway(self.conversation_id)
        if gateway is None:
            return SafeRuntimeResult(
                False,
                operation,
                error="Browser gateway is not bound to this conversation",
            )
        action = operation.split(".", 1)[1]
        try:
            data = gateway.execute(action, dict(args))
        except Exception as exc:
            return SafeRuntimeResult(False, operation, error=f"Browser action failed: {exc}")
        return SafeRuntimeResult(
            True,
            operation,
            data=data,
            metadata={
                "browser_action": action,
                "visual_input_pending": bool(
                    isinstance(data, dict) and data.get("image_attached") is True
                ),
            },
        )

    def _op_registry_tool(
        self,
        operation: str,
        tool_name: str,
        args: dict,
        turn_id: str,
        origin: str,
        security_context,
        grant=None,
        expected_approval_id=None,
        automatic_budget_reserved: bool = False,
    ) -> SafeRuntimeResult:
        if not self.registry:
            return SafeRuntimeResult(False, operation, error="No tool registry attached")
        tool_result = self.registry.execute_tool(
            tool_name,
            args,
            turn_id=turn_id,
            conversation_id=self.conversation_id,
            workspace_id=self.workspace_id,
            origin=origin,
            grant=grant,
            expected_approval_id=expected_approval_id,
            security_context=security_context,
            automatic_budget_reserved=automatic_budget_reserved,
        )
        metadata = dict(getattr(tool_result, "metadata", {}) or {})
        handles: list[str] = []
        if tool_name == "artifact_store" and metadata.get("artifact_id"):
            handles.append(f"artifact:{metadata['artifact_id']}")
        if tool_name == "child_spawn" and metadata.get("child_id"):
            handles.append(f"child:{metadata['child_id']}")
        requires_approval = bool(getattr(tool_result, "requires_approval", False))
        approval_payload = metadata.get("approval_payload")
        if requires_approval and not isinstance(approval_payload, dict):
            approval_payload = dict(args)
        approval_action = metadata.get("approval_action")
        if requires_approval and not approval_action:
            approval_action = tool_name
        resume_tool_name = metadata.get("resume_tool_name")
        if requires_approval and not resume_tool_name:
            resume_tool_name = tool_name

        return SafeRuntimeResult(
            success=tool_result.success,
            operation=operation,
            data=tool_result.output,
            error=tool_result.error,
            context_handles=handles,
            requires_approval=requires_approval,
            approval_action=(
                str(approval_action) if requires_approval and approval_action else None
            ),
            approval_payload=approval_payload if requires_approval else None,
            required_capability=(
                str(metadata.get("required_capability"))
                if requires_approval and metadata.get("required_capability")
                else None
            ),
            resume_tool_name=(
                str(resume_tool_name)
                if requires_approval and resume_tool_name
                else None
            ),
            metadata={"effective_tool_name": tool_name, **metadata},
        )

    def _op_repo_read(self, args, turn_id, origin, security_context):
        delegated = dict(args)
        try:
            start_line = max(1, int(delegated.get("start_line", 1) or 1))
        except (TypeError, ValueError):
            start_line = 1
        if delegated.get("end_line") is None and not delegated.get("around_symbol"):
            delegated["end_line"] = start_line + 99
        delegated.setdefault("max_tokens", 800)

        result = self._op_registry_tool(
            "repo.read", "read_file", delegated, turn_id, origin, security_context
        )
        if result.success:
            metadata = dict(result.metadata or {})
            end_line = metadata.get("end_line", delegated.get("end_line", start_line + 99))
            result.context_handles = [
                f"ctx:file:{delegated.get('path', '')}:{start_line}-{end_line}"
            ]
            metadata["aci_window_lines"] = (
                int(end_line) - start_line + 1 if end_line is not None else 100
            )
            result.metadata = metadata
        return result

    def _op_repo_search(self, args, turn_id, origin, security_context):
        query = str(args.get("query", args.get("pattern", ""))).strip()
        if not query:
            return SafeRuntimeResult(False, "repo.search", error="query or pattern required")

        max_tokens = _runtime_token_budget(args, 1200)
        max_results = _runtime_int(args.get("max_results", args.get("limit", 50)), 50, 1, 500)
        max_per_file = _runtime_int(args.get("max_per_file", 8), 8, 1, 100)
        regex_mode = bool(args.get("regex", False))
        case_sensitive = bool(args.get("case_sensitive", False))

        if not regex_mode and self.registry and self.index is not None:
            from kitt.tools.handlers.search import indexed_literal_search

            self.registry._refresh_index()
            data = indexed_literal_search(
                self.index,
                query,
                path_allowed=lambda path: (
                    security_context is None or security_context.allows_path(path)
                ),
                case_sensitive=case_sensitive,
                max_results=max_results,
                max_per_file=max_per_file,
                token_budget=max_tokens,
            )
            if data is not None:
                return SafeRuntimeResult(
                    True,
                    "repo.search",
                    data=data,
                    tokens_saved=max(0, int(data.get("omitted_matches", 0)) * 8),
                    metadata={
                        "backend": "index",
                        "index_state": data.get("index_state", "UNKNOWN"),
                        "max_tokens": max_tokens,
                    },
                )

        engine = getattr(self.registry, "native_engine", None) if self.registry else None
        if engine is not None and not getattr(security_context, "is_path_scoped", False):
            data = engine.search(
                query,
                regex=regex_mode,
                case_sensitive=case_sensitive,
                max_results=max_results,
                max_per_file=max_per_file,
                context_lines=_runtime_int(args.get("context_lines", 1), 1, 0, 8),
                token_budget=max_tokens,
            )
            return SafeRuntimeResult(
                True,
                "repo.search",
                data=data,
                tokens_saved=max(0, int(data.get("omitted_matches", 0)) * 8),
                metadata={"backend": engine.status.backend, "max_tokens": max_tokens},
            )

        delegated = dict(args)
        delegated["pattern"] = query
        delegated["max_tokens"] = max_tokens
        delegated.pop("token_budget", None)
        return self._op_registry_tool(
            "repo.search", "search", delegated, turn_id, origin, security_context
        )

    def _op_repo_context_map(self, args, security_context):
        if self.registry is not None:
            self.registry._refresh_index()
        data = self.context_map_builder.build(
            args,
            conversation_id=self.conversation_id,
            security_context=security_context,
        )
        encoded = str(data).encode("utf-8")
        return SafeRuntimeResult(
            True,
            "repo.context_map",
            data=data,
            context_handles=["ctx:context-map"],
            metadata={
                "backend": "repository_index",
                "output_family": "context_map",
                "output_estimated_tokens": (len(encoded) + 3) // 4,
            },
        )

    def _op_flow_execute(
        self,
        args,
        turn_id,
        origin,
        capabilities,
        security_context,
    ):
        return self.programmatic_flow.execute(
            args,
            turn_id=turn_id,
            origin=origin,
            capabilities=set(capabilities),
            security_context=security_context,
        )

    def _op_program_execute(
        self,
        args,
        turn_id,
        origin,
        capabilities,
        security_context,
    ):
        return self.program_runtime.execute(
            args,
            turn_id=turn_id,
            origin=origin,
            capabilities=set(capabilities),
            security_context=security_context,
        )

    def _op_program_session_execute(
        self, args, turn_id, origin, capabilities, security_context
    ):
        name = str(args.get("name") or "default")
        return self.program_sessions.execute(
            name,
            args,
            turn_id=turn_id,
            origin=origin,
            capabilities=capabilities,
            security_context=security_context,
        )

    def _op_program_session_get(self, args):
        name = str(args.get("name") or "default")
        return SafeRuntimeResult(
            True,
            "program.session.get",
            data=self.program_sessions.snapshot(name),
        )

    def _op_program_session_clear(self, args):
        name = str(args.get("name") or "default")
        return SafeRuntimeResult(
            True,
            "program.session.clear",
            data={"name": name, "cleared": self.program_sessions.clear(name)},
        )

    def _op_repo_inspect_symbol(self, args, turn_id, origin, security_context):
        symbol = str(args.get("symbol", "")).strip()
        if not symbol:
            return SafeRuntimeResult(False, "repo.inspect_symbol", error="Symbol argument required")

        max_tokens = _runtime_token_budget(args, 1200)
        total_budget_bytes = max_tokens * 4
        engine = getattr(self.registry, "native_engine", None) if self.registry else None
        if engine is not None:
            native_matches = engine.find_symbols(symbol, limit=5)
            safe_matches = []
            for item in native_matches:
                try:
                    self._assert_native_path_allowed(security_context, item["path"])
                except PermissionError:
                    continue
                safe_matches.append(item)

            if safe_matches:
                snippets = []
                remaining = total_budget_bytes
                omitted_source_bytes = 0
                for item in safe_matches[:3]:
                    if remaining <= 128:
                        break
                    read = engine.read_symbol(item["id"])
                    if not read:
                        continue
                    source = str(read.get("source", "") or "")
                    source_budget = max(0, remaining - 128)
                    bounded = _truncate_utf8(source, source_budget)
                    omitted_source_bytes += max(
                        0, len(source.encode("utf-8")) - len(bounded.encode("utf-8"))
                    )
                    snippets.append(
                        {
                            "symbol": item["name"],
                            "path": item["path"],
                            "kind": item["kind"],
                            "lines": f"{item['start_line']}-{item['end_line']}",
                            "content": bounded,
                            "truncated": bounded != source,
                        }
                    )
                    remaining -= min(remaining, len(bounded.encode("utf-8")) + 128)

                return SafeRuntimeResult(
                    True,
                    "repo.inspect_symbol",
                    data={"symbol": symbol, "matches": safe_matches, "snippets": snippets},
                    context_handles=[f"ctx:repo:{symbol}"],
                    tokens_saved=omitted_source_bytes // 4,
                    metadata={
                        "backend": engine.status.backend,
                        "symbol_index": getattr(engine, "symbol_index_status", lambda: {})(),
                        "max_tokens": max_tokens,
                        "truncated": omitted_source_bytes > 0 or len(snippets) < min(3, len(safe_matches)),
                    },
                )

        handle_info = self.handles.resolve(
            f"ctx:repo:{symbol}", security_context=security_context
        )
        symbols = handle_info.get("symbols", [])
        snippets = []
        per_snippet_tokens = max(64, max_tokens // 3)
        for item in symbols[:3]:
            path = item.get("path", "")
            start = max(1, int(item.get("start_line", 1)) - 5)
            end = int(item.get("end_line", start + 30)) + 5
            read_result = self._op_repo_read(
                {
                    "path": path,
                    "start_line": start,
                    "end_line": end,
                    "max_tokens": per_snippet_tokens,
                },
                turn_id,
                origin,
                security_context,
            )
            if read_result.success:
                snippets.append(
                    {
                        "symbol": item.get("name", symbol),
                        "path": path,
                        "kind": item.get("kind", ""),
                        "lines": f"{start}-{end}",
                        "content": read_result.data,
                    }
                )
        content_bytes = sum(
            len(str(item.get("content", "")).encode("utf-8")) for item in snippets
        )
        return SafeRuntimeResult(
            True,
            "repo.inspect_symbol",
            data={"symbol": symbol, "matches": symbols, "snippets": snippets},
            context_handles=[f"ctx:repo:{symbol}"],
            metadata={
                "backend": "handles",
                "max_tokens": max_tokens,
                "estimated_tokens": (content_bytes + 3) // 4,
            },
        )

    def _resolve_native_symbol(self, value: str, security_context=None):
        engine = getattr(self.registry, "native_engine", None) if self.registry else None
        if engine is None:
            return None, None
        if "::" in value and "." in value.split("::", 1)[0]:
            relative = WorkspaceFileSystem(self.root).relative(value.split("::", 1)[0])
            self._assert_native_path_allowed(security_context, relative)
        direct = engine.read_symbol(value)
        if direct:
            return engine, direct
        matches = engine.find_symbols(value, limit=5)
        if not matches:
            return engine, None
        return engine, engine.read_symbol(matches[0]["id"])

    @staticmethod
    def _assert_native_path_allowed(security_context, path: str) -> None:
        if security_context is not None:
            security_context.assert_path_allowed(path)

    def _op_repo_read_symbol(self, args, security_context):
        value = str(args.get("symbol_id", args.get("symbol", ""))).strip()
        if not value:
            return SafeRuntimeResult(False, "repo.read_symbol", error="symbol or symbol_id required")
        engine, found = self._resolve_native_symbol(value, security_context)
        if engine is None:
            return SafeRuntimeResult(False, "repo.read_symbol", error="native code engine unavailable")
        if not found:
            return SafeRuntimeResult(False, "repo.read_symbol", error=f"symbol not found: {value}")
        self._assert_native_path_allowed(security_context, found["symbol"]["path"])
        max_tokens = _runtime_token_budget(args, 1200)
        bounded = _bounded_symbol_payload(found, max_tokens)
        return SafeRuntimeResult(
            True,
            "repo.read_symbol",
            data=bounded,
            context_handles=[f"ctx:repo:{found['symbol']['id']}"],
            metadata={"max_tokens": max_tokens, "truncated": bounded.get("truncated", False),
                      "symbol_index": getattr(engine, "symbol_index_status", lambda: {})()},
        )

    def _op_repo_references(self, args, security_context):
        value = str(args.get("symbol_id", args.get("symbol", ""))).strip()
        if not value:
            return SafeRuntimeResult(False, "repo.references", error="symbol or symbol_id required")
        engine = getattr(self.registry, "native_engine", None) if self.registry else None
        if engine is None:
            return SafeRuntimeResult(False, "repo.references", error="native code engine unavailable")

        limit = _runtime_int(args.get("limit", 100), 100, 1, 500)
        max_tokens = _runtime_token_budget(args, 1200)
        rows = engine.references(value, limit)
        allowed = []
        for row in rows:
            try:
                self._assert_native_path_allowed(security_context, row["path"])
            except PermissionError:
                continue
            allowed.append(row)

        budget_bytes = max_tokens * 4
        bounded = []
        used = 0
        for row in allowed:
            estimated = sum(
                len(str(key).encode("utf-8")) + len(str(value).encode("utf-8"))
                for key, value in row.items()
            ) + 16
            if bounded and used + estimated > budget_bytes:
                break
            bounded.append(row)
            used += estimated
            if used >= budget_bytes:
                break

        return SafeRuntimeResult(
            True,
            "repo.references",
            data=bounded,
            metadata={
                "max_tokens": max_tokens,
                "returned": len(bounded),
                "total_allowed": len(allowed),
                "symbol_index": getattr(engine, "symbol_index_status", lambda: {})(),
                "truncated": len(bounded) < len(allowed),
            },
        )

    def _op_repo_lsp(
        self,
        operation: str,
        method: str,
        args: dict,
        security_context,
    ) -> SafeRuntimeResult:
        path = str(args.get("path") or args.get("file") or "").strip()
        if not path:
            return SafeRuntimeResult(False, operation, error="path is required")
        self._assert_native_path_allowed(security_context, path)
        try:
            result = LanguageServerClient(self.root).request(
                path,
                method,
                line=_runtime_int(args.get("line", 1), 1, 1, 10_000_000),
                column=_runtime_int(args.get("column", 0), 0, 0, 10_000_000),
                timeout_seconds=float(args.get("timeout_seconds", 8.0) or 8.0),
            )
        except (FileNotFoundError, PermissionError, TimeoutError, RuntimeError) as exc:
            return SafeRuntimeResult(False, operation, error=str(exc))
        return SafeRuntimeResult(True, operation, data=result)

    def _op_repo_call_hierarchy(
        self,
        args: dict,
        security_context,
    ) -> SafeRuntimeResult:
        path = str(args.get("path") or args.get("file") or "").strip()
        if not path:
            return SafeRuntimeResult(
                False, "repo.call_hierarchy", error="path is required"
            )
        self._assert_native_path_allowed(security_context, path)
        line = _runtime_int(args.get("line", 1), 1, 1, 10_000_000)
        column = _runtime_int(args.get("column", 0), 0, 0, 10_000_000)
        direction = str(args.get("direction") or "incoming").strip().lower()
        if direction not in {"incoming", "outgoing"}:
            return SafeRuntimeResult(
                False,
                "repo.call_hierarchy",
                error="direction must be incoming or outgoing",
            )
        client = LanguageServerClient(self.root)
        try:
            prepared = client.request(
                path,
                "textDocument/prepareCallHierarchy",
                line=line,
                column=column,
            )
            items = prepared.get("result") if isinstance(prepared, dict) else None
            if (
                not isinstance(prepared, dict)
                or not prepared.get("available")
                or not isinstance(items, list)
                or not items
            ):
                return SafeRuntimeResult(
                    True, "repo.call_hierarchy", data=prepared
                )
            calls = client.request(
                path,
                f"callHierarchy/{direction}Calls",
                params={"item": items[0]},
            )
        except (FileNotFoundError, PermissionError, TimeoutError, RuntimeError) as exc:
            return SafeRuntimeResult(
                False, "repo.call_hierarchy", error=str(exc)
            )
        return SafeRuntimeResult(
            True,
            "repo.call_hierarchy",
            data={
                "backend": prepared.get("backend"),
                "available": True,
                "direction": direction,
                "item": items[0],
                "calls": calls.get("result"),
            },
        )

    def _canonicalize_repo_edit_args(self, args: dict, security_context) -> dict:
        value = str(args.get("symbol_id", args.get("symbol", ""))).strip()
        replacement = args.get("replacement")
        if not value or not isinstance(replacement, str):
            raise ValueError("symbol_id/symbol and replacement are required")
        engine, found = self._resolve_native_symbol(value, security_context)
        if engine is None or not found:
            raise KeyError(f"symbol not found: {value}")
        symbol = found["symbol"]
        self._assert_native_path_allowed(security_context, symbol["path"])
        expected = args.get("expected_hash")
        if expected is not None and str(expected) != str(symbol["source_hash"]):
            raise RuntimeError("optimistic edit conflict: supplied symbol hash is stale")
        normalized = dict(args)
        normalized["symbol_id"] = symbol["id"]
        normalized["path"] = symbol["path"]
        normalized["expected_hash"] = symbol["source_hash"]
        normalized.pop("symbol", None)
        return normalized

    def _op_repo_edit_symbol(self, args, turn_id, security_context):
        symbol_id = str(args.get("symbol_id", "")).strip()
        replacement = args.get("replacement")
        expected_hash = str(args.get("expected_hash", "")).strip()
        expected_path = str(args.get("path", "")).strip()
        if not symbol_id or not isinstance(replacement, str) or not expected_hash:
            return SafeRuntimeResult(False, "repo.edit_symbol", error="canonical structural edit arguments missing")
        engine = getattr(self.registry, "native_engine", None) if self.registry else None
        if engine is None:
            return SafeRuntimeResult(False, "repo.edit_symbol", error="native code engine unavailable")
        found = engine.read_symbol(symbol_id)
        if not found:
            return SafeRuntimeResult(False, "repo.edit_symbol", error=f"symbol no longer exists: {symbol_id}")
        symbol = found["symbol"]
        path = str(symbol["path"])
        if expected_path and path != expected_path:
            return SafeRuntimeResult(False, "repo.edit_symbol", error="structural edit target path changed after approval")
        self._assert_native_path_allowed(security_context, path)
        coordinator = getattr(self.registry, "coordinator", None) if self.registry else None
        if coordinator is not None and security_context is not None and getattr(security_context, "principal_type", "") == "CHILD":
            coordinator.claim_symbol_for_edit(
                symbol_id, security_context.principal_id,
                str(args.get("intent", f"edit {symbol_id}")),
            )

        fs = WorkspaceFileSystem(self.root, max_file_bytes=DEFAULT_MAX_FILE_BYTES)
        try:
            before = fs.read(path, max_bytes=DEFAULT_MAX_FILE_BYTES)
            before_text = before.content.decode("utf-8", errors="strict")
        except Exception as exc:
            return SafeRuntimeResult(False, "repo.edit_symbol", error=f"structural edit snapshot failed: {exc}")

        result = engine.replace_symbol(
            symbol_id, replacement, expected_hash=expected_hash,
            validate_syntax=bool(args.get("validate_syntax", True)),
        )
        if not (self.registry and result.get("changed")):
            return SafeRuntimeResult(True, "repo.edit_symbol", data=result)

        formatting = self.registry.formatting_engine.format_paths([path]).get(path, {})
        try:
            final_data = fs.read(path, max_bytes=DEFAULT_MAX_FILE_BYTES)
            final_text = final_data.content.decode("utf-8", errors="strict")
        except Exception as exc:
            return SafeRuntimeResult(False, "repo.edit_symbol", error=f"formatted structural edit could not be read: {exc}")

        report = self.registry.post_edit_validator.validate_paths([path])
        if not report.ok:
            try:
                fs.atomic_write(
                    path,
                    before_text,
                    expected_exists=True,
                    expected_sha256=final_data.sha256,
                    max_bytes=DEFAULT_MAX_FILE_BYTES,
                )
                self.registry._refresh_index([path])
            except Exception as exc:
                return SafeRuntimeResult(
                    False,
                    "repo.edit_symbol",
                    error=f"Post-edit validation failed and rollback failed: {exc}",
                )
            failures = [
                f"{item.path} [{item.validator}]: {item.message}"
                for item in report.diagnostics if not item.ok
            ]
            return SafeRuntimeResult(
                False,
                "repo.edit_symbol",
                error="Post-edit validation failed; structural edit was reverted. " + " | ".join(failures[:8]),
                metadata={"post_edit_gate": report.as_dict(), "formatting": formatting},
            )

        try:
            changeset = self.registry.applier.tracker.record_changeset(
                description=f"repo.edit_symbol {path}",
                snapshots=[FileSnapshot(path, True, before_text)],
                workspace_id=self.workspace_id,
                conversation_id=self.conversation_id,
                turn_id=turn_id,
                post_hashes={path: final_data.sha256},
                post_exists={path: True},
                post_contents={path: final_text},
            )
        except Exception as exc:
            try:
                fs.atomic_write(
                    path,
                    before_text,
                    expected_exists=True,
                    expected_sha256=final_data.sha256,
                    max_bytes=DEFAULT_MAX_FILE_BYTES,
                )
            except Exception:
                pass
            return SafeRuntimeResult(False, "repo.edit_symbol", error=f"Undo journal persistence failed: {exc}")

        result["formatting"] = formatting
        result["post_edit_gate"] = report.as_dict()
        result["changeset_id"] = getattr(changeset, "id", None)
        self.registry._refresh_index([path])
        recorder = getattr(self.registry, "record_changed_paths", None)
        if recorder is not None:
            recorder(
                conversation_id=self.conversation_id, turn_id=turn_id,
                changed=[path], kind="native_symbol_edit",
            )
        return SafeRuntimeResult(True, "repo.edit_symbol", data=result)

    def _op_memory_correct(self, args):
        if not self.memory or not hasattr(self.memory, "remember_correction"):
            return SafeRuntimeResult(False, "memory.correct", error="kitt-memory service unavailable")
        context = str(args.get("context", "")).strip()
        predicted = str(args.get("predicted", "")).strip()
        corrected = str(args.get("corrected", "")).strip()
        if not context or not predicted or not corrected:
            return SafeRuntimeResult(False, "memory.correct", error="context, predicted and corrected are required")
        cid = self.memory.remember_correction(context, predicted, corrected, args.get("reason"), str(args.get("source", "agent")))
        return SafeRuntimeResult(True, "memory.correct", data={"id": cid})

    def _op_memory_concept(self, args):
        if not self.memory or not hasattr(self.memory, "remember_concept"):
            return SafeRuntimeResult(False, "memory.concept", error="hybrid memory service unavailable")
        name = str(args.get("name", "")).strip(); definition = str(args.get("definition", "")).strip()
        if not name or not definition:
            return SafeRuntimeResult(False, "memory.concept", error="name and definition are required")
        data = self.memory.remember_concept(
            name, definition, float(args.get("confidence", 0.7) or 0.7),
            args.get("labels", []) or [], args.get("source_memory_ids", []) or [],
        )
        return SafeRuntimeResult(True, "memory.concept", data=data)

    def _op_memory_link(self, args):
        if not self.memory or not hasattr(self.memory, "link_concepts"):
            return SafeRuntimeResult(False, "memory.link", error="hybrid memory service unavailable")
        source_id = str(args.get("source_id", "")).strip(); target_id = str(args.get("target_id", "")).strip()
        relation = str(args.get("relation", "RELATED_TO")).strip()
        if not source_id or not target_id:
            return SafeRuntimeResult(False, "memory.link", error="source_id and target_id are required")
        link_id = self.memory.link_concepts(source_id, target_id, relation, float(args.get("weight", 1.0) or 1.0))
        return SafeRuntimeResult(True, "memory.link", data={"id": link_id})

    def _op_children_send(self, args):
        recipient = str(args.get("recipient") or args.get("child_id") or "").strip()
        sender = str(args.get("sender") or "parent").strip()
        message = args.get("message", "")
        if not recipient or message in (None, ""):
            return SafeRuntimeResult(
                False,
                "children.send",
                error="recipient/child_id and message required",
            )
        if not self.children or not hasattr(self.children, "send_agent_message"):
            return SafeRuntimeResult(
                False,
                "children.send",
                error="Agent family messaging not available",
            )
        message_object, delivery_mode = self.children.send_agent_message(
            self.conversation_id,
            sender=sender,
            recipient=recipient,
            message=message,
            delivery_mode=str(args.get("delivery_mode") or "AUTO"),
            correlation_id=args.get("correlation_id"),
            reply_to=args.get("reply_to"),
            trace_id=args.get("trace_id"),
        )
        return SafeRuntimeResult(
            True,
            "children.send",
            data={
                "message_id": getattr(message_object, "id", ""),
                "status": "SENT",
                "delivery_mode": delivery_mode,
                "sender": sender,
                "recipient": recipient,
            },
            context_handles=[f"child:{recipient}"],
        )

    def _op_children_inspect(self, args):
        child_id = str(args.get("child_id", ""))
        if not child_id:
            return SafeRuntimeResult(False, "children.inspect", error="child_id required")
        if not self.children:
            return SafeRuntimeResult(False, "children.inspect", error="Child manager not attached")
        child = self.children.inspect(
            child_id,
            conversation_id=self.conversation_id,
            workspace_id=self.workspace_id,
        )
        if not child:
            return SafeRuntimeResult(False, "children.inspect", error=f"Child {child_id} not found")
        return SafeRuntimeResult(
            True,
            "children.inspect",
            data={
                "id": child.id,
                "name": child.name,
                "state": child.state,
                "task": child.task,
                "result_artifact_id": child.result_artifact_id,
                "error": child.error,
            },
            context_handles=[f"child:{child_id}"],
        )

    def _op_children_observe(self):
        if not self.children or not hasattr(self.children, "observe_agents"):
            return SafeRuntimeResult(
                False,
                "children.observe",
                error="Agent roster not available",
            )
        return SafeRuntimeResult(
            True,
            "children.observe",
            data=self.children.observe_agents(self.conversation_id),
        )

    def _op_children_passivate(self, args):
        child_id = str(args.get("child_id") or "").strip()
        if not child_id:
            return SafeRuntimeResult(
                False, "children.passivate", error="child_id required"
            )
        if not self.children:
            return SafeRuntimeResult(
                False, "children.passivate", error="Child manager not attached"
            )
        ok = self.children.passivate(
            child_id,
            conversation_id=self.conversation_id,
            workspace_id=self.workspace_id,
        )
        return SafeRuntimeResult(
            ok,
            "children.passivate",
            data={"child_id": child_id, "state": "PASSIVATED"} if ok else None,
            error=None if ok else f"Child {child_id} not found",
        )

    def _op_children_revive(self, args):
        child_id = str(args.get("child_id") or "").strip()
        if not child_id:
            return SafeRuntimeResult(
                False, "children.revive", error="child_id required"
            )
        if not self.children:
            return SafeRuntimeResult(
                False, "children.revive", error="Child manager not attached"
            )
        child = self.children.revive(
            child_id,
            task=args.get("task"),
            conversation_id=self.conversation_id,
            workspace_id=self.workspace_id,
            timeout_seconds=args.get("timeout_seconds"),
        )
        return SafeRuntimeResult(
            True,
            "children.revive",
            data={"child_id": child.id, "state": child.state, "task": child.task},
            context_handles=[f"child:{child.id}"],
        )

    def _op_goal_inspect(self, args):
        goal_id = str(args.get("goal_id", ""))
        if not goal_id:
            return SafeRuntimeResult(False, "goal.inspect", error="goal_id required")
        if not self.goals:
            return SafeRuntimeResult(False, "goal.inspect", error="Goal service not attached")
        goal = self.goals.get_scoped(goal_id, self.conversation_id)
        if not goal:
            return SafeRuntimeResult(False, "goal.inspect", error=f"Goal {goal_id} not found")
        return SafeRuntimeResult(
            True,
            "goal.inspect",
            data={
                "id": goal.id,
                "objective": goal.objective,
                "state": goal.state,
                "turns_used": goal.turns_used,
                "tokens_used": goal.tokens_used,
                "success_criteria": goal.success_criteria,
            },
            context_handles=[f"goal:{goal_id}"],
        )

    def _op_goal_update(self, args):
        goal_id = str(args.get("goal_id", ""))
        state = str(args.get("state", ""))
        if not goal_id or not state:
            return SafeRuntimeResult(False, "goal.update", error="goal_id and state required")
        if not self.goals:
            return SafeRuntimeResult(False, "goal.update", error="Goal service not attached")
        goal = self.goals.update_state(
            goal_id,
            state,
            last_error=args.get("last_error"),
            conversation_id=self.conversation_id,
        )
        return SafeRuntimeResult(
            bool(goal),
            "goal.update",
            data={"goal_id": goal_id, "state": state},
            context_handles=[f"goal:{goal_id}"],
        )

    def _scoped_artifact(self, artifact_id: str):
        if not self.artifacts:
            raise RuntimeError("Artifact store not attached")
        artifact = self.artifacts.get(artifact_id)
        if artifact is None:
            raise KeyError(artifact_id)
        if artifact.workspace_id != self.workspace_id:
            raise PermissionError("Cross-workspace artifact access blocked")
        if artifact.conversation_id not in (None, self.conversation_id):
            raise PermissionError("Cross-conversation artifact access blocked")
        return artifact

    def _op_artifacts_search(self, args):
        artifact_id = str(args.get("artifact_id") or "").strip()
        query = str(args.get("query") or "").strip()
        if not artifact_id or not query:
            return SafeRuntimeResult(False, "artifacts.search", error="artifact_id and query are required")
        try:
            self._scoped_artifact(artifact_id)
            hits = self.artifacts.search_text(
                artifact_id,
                query,
                limit=_runtime_int(args.get("limit", 20), 20, 1, 100),
                context_chars=_runtime_int(args.get("context_chars", 160), 160, 40, 2000),
            )
        except Exception as exc:
            return SafeRuntimeResult(False, "artifacts.search", error=str(exc))
        return SafeRuntimeResult(True, "artifacts.search", data=hits, context_handles=[f"artifact:{artifact_id}"])

    def _op_artifacts_hydrate(self, args):
        artifact_id = str(args.get("artifact_id") or "").strip()
        if not artifact_id:
            return SafeRuntimeResult(False, "artifacts.hydrate", error="artifact_id is required")
        try:
            self._scoped_artifact(artifact_id)
            page = self.artifacts.read_text_page(
                artifact_id,
                offset=_runtime_int(args.get("offset", 0), 0, 0, 2_147_483_647),
                max_bytes=_runtime_int(args.get("max_bytes", 32768), 32768, 1024, 32768),
            )
        except Exception as exc:
            return SafeRuntimeResult(False, "artifacts.hydrate", error=str(exc))
        return SafeRuntimeResult(True, "artifacts.hydrate", data=page, context_handles=[f"artifact:{artifact_id}"])

    def _op_artifacts_retrieve(self, args):
        artifact_id = str(args.get("artifact_id") or "").strip()
        if not artifact_id:
            return SafeRuntimeResult(
                False,
                "artifacts.retrieve",
                error="artifact_id is required",
            )
        max_tokens = _runtime_token_budget(args, 1200)
        requested_bytes = _runtime_int(
            args.get("max_bytes", max_tokens * 4),
            max_tokens * 4,
            256,
            32768,
        )
        max_bytes = min(requested_bytes, max_tokens * 4, 32768)
        offset = _runtime_int(args.get("offset", 0), 0, 0, 2_147_483_647)
        query = str(args.get("query") or "").strip()
        try:
            self._scoped_artifact(artifact_id)
            if query:
                hits = self.artifacts.search_text(
                    artifact_id,
                    query,
                    limit=_runtime_int(args.get("limit", 20), 20, 1, 100),
                    context_chars=_runtime_int(
                        args.get("context_chars", 160),
                        160,
                        40,
                        min(2000, max_bytes),
                    ),
                )
                encoded = json.dumps(
                    hits,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                )
                bounded = _truncate_utf8(encoded, max_bytes)
                data = {
                    "artifact_id": artifact_id,
                    "query": query,
                    "hits_json": bounded,
                    "truncated": len(bounded.encode("utf-8")) < len(encoded.encode("utf-8")),
                }
            else:
                page = self.artifacts.read_text_page(
                    artifact_id,
                    offset=offset,
                    max_bytes=max_bytes,
                )
                data = {
                    **page,
                    "artifact_id": artifact_id,
                    "max_tokens": max_tokens,
                }
        except Exception as exc:
            return SafeRuntimeResult(False, "artifacts.retrieve", error=str(exc))
        return SafeRuntimeResult(
            True,
            "artifacts.retrieve",
            data=data,
            context_handles=[f"artifact:{artifact_id}"],
            metadata={
                "max_tokens": max_tokens,
                "max_bytes": max_bytes,
                "query_mode": bool(query),
            },
        )

    def _op_surface_capabilities(self):
        if not self.surfaces:
            return SafeRuntimeResult(False, "surface.capabilities", error="Surface service not attached")
        return SafeRuntimeResult(True, "surface.capabilities", data=self.surfaces.capabilities())

    def _op_surface_publish(self, args):
        if not self.surfaces:
            return SafeRuntimeResult(False, "surface.publish", error="Surface service not attached")
        spec = args.get("surface") if isinstance(args.get("surface"), dict) else args
        return SafeRuntimeResult(True, "surface.publish", data=self.surfaces.publish(dict(spec)))

    def _op_surface_patch(self, args):
        if not self.surfaces:
            return SafeRuntimeResult(False, "surface.patch", error="Surface service not attached")
        return SafeRuntimeResult(
            True,
            "surface.patch",
            data=self.surfaces.patch(
                str(args.get("surface_id") or ""),
                int(args.get("base_revision", -1)),
                list(args.get("operations") or []),
            ),
        )

    def _op_surface_get(self, args):
        if not self.surfaces:
            return SafeRuntimeResult(False, "surface.get", error="Surface service not attached")
        surface_id = str(args.get("surface_id") or "").strip()
        data = self.surfaces.get(surface_id)
        return SafeRuntimeResult(
            data is not None,
            "surface.get",
            data=data,
            error=None if data is not None else "surface not found",
        )

    def _op_surface_delete(self, args):
        if not self.surfaces:
            return SafeRuntimeResult(False, "surface.delete", error="Surface service not attached")
        surface_id = str(args.get("surface_id") or "").strip()
        return SafeRuntimeResult(True, "surface.delete", data={"deleted": self.surfaces.delete(surface_id)})

    def _op_surface_action(self, args):
        if not self.surfaces:
            return SafeRuntimeResult(False, "surface.action", error="Surface service not attached")
        data = self.surfaces.action(
            str(args.get("surface_id") or ""),
            str(args.get("component_id") or ""),
            str(args.get("action") or ""),
            dict(args.get("context") or {}),
        )
        return SafeRuntimeResult(True, "surface.action", data=data)

    @staticmethod
    def _backend_module(args):
        module = args.get("backend") if isinstance(args.get("backend"), dict) else args.get("module")
        if not isinstance(module, dict):
            raise ValueError("backend/module must be an object")
        return module

    def _op_backend_validate(self, args):
        if not self.backend:
            return SafeRuntimeResult(False, "backend.validate", error="Backend IR service not attached")
        try:
            module = self._backend_module(args)
        except ValueError as exc:
            return SafeRuntimeResult(False, "backend.validate", error=str(exc))
        issues = [issue.to_dict() for issue in self.backend.validate(module)]
        return SafeRuntimeResult(
            True,
            "backend.validate",
            data={"valid": not any(item["severity"] == "error" for item in issues), "issues": issues},
        )

    def _op_backend_plan(self, args):
        if not self.backend:
            return SafeRuntimeResult(False, "backend.plan", error="Backend IR service not attached")
        try:
            plan = self.backend.plan(self._backend_module(args))
        except (TypeError, ValueError) as exc:
            return SafeRuntimeResult(False, "backend.plan", error=str(exc))
        return SafeRuntimeResult(True, "backend.plan", data=plan.to_dict())

    def _op_backend_compile(self, args):
        if not self.backend:
            return SafeRuntimeResult(False, "backend.compile", error="Backend IR service not attached")
        try:
            data = self.backend.compile(
                self._backend_module(args),
                str(args.get("target") or "python"),
            )
        except (TypeError, ValueError) as exc:
            return SafeRuntimeResult(False, "backend.compile", error=str(exc))
        return SafeRuntimeResult(
            bool(data.get("ok")),
            "backend.compile",
            data=data,
            error=None if data.get("ok") else "backend validation failed",
        )

    def _op_schedule_create(self, args):
        if self.scheduler is None:
            return SafeRuntimeResult(
                False, "schedule.create", error="Persistent scheduler not attached"
            )
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            return SafeRuntimeResult(False, "schedule.create", error="prompt required")
        schedule_id = self.scheduler.schedule(
            workspace_id=self.workspace_id,
            conversation_id=self.conversation_id,
            prompt=prompt,
            run_at=args.get("run_at"),
            interval_seconds=args.get("interval_seconds"),
            cron_expr=args.get("cron_expr"),
        )
        return SafeRuntimeResult(
            True, "schedule.create", data={"id": schedule_id}
        )

    def _op_schedule_list(self, args):
        if self.scheduler is None:
            return SafeRuntimeResult(
                False, "schedule.list", error="Persistent scheduler not attached"
            )
        return SafeRuntimeResult(
            True,
            "schedule.list",
            data=self.scheduler.list(
                self.conversation_id,
                limit=int(args.get("limit", 100) or 100),
            ),
        )

    def _op_schedule_cancel(self, args):
        if self.scheduler is None:
            return SafeRuntimeResult(
                False, "schedule.cancel", error="Persistent scheduler not attached"
            )
        schedule_id = str(args.get("id") or "").strip()
        if not schedule_id:
            return SafeRuntimeResult(False, "schedule.cancel", error="id required")
        return SafeRuntimeResult(
            True,
            "schedule.cancel",
            data={"id": schedule_id, "cancelled": self.scheduler.cancel(schedule_id)},
        )

    def _op_heartbeat_set(self, args):
        if self.scheduler is None:
            return SafeRuntimeResult(
                False, "heartbeat.set", error="Persistent scheduler not attached"
            )
        prompt = str(
            args.get("prompt")
            or "Continue the active goal from current evidence."
        ).strip()
        interval = float(args.get("interval_seconds", 60) or 60)
        schedule_id = self.scheduler.set_heartbeat(
            workspace_id=self.workspace_id,
            conversation_id=self.conversation_id,
            prompt=prompt,
            interval_seconds=interval,
        )
        return SafeRuntimeResult(
            True,
            "heartbeat.set",
            data={"id": schedule_id, "interval_seconds": max(5.0, interval)},
        )

    def _op_memory_query(self, args):
        if not self.memory:
            return SafeRuntimeResult(False, "memory.query", error="Memory service not attached")
        query = str(args.get("query", ""))
        limit = _runtime_int(args.get("limit", 5), 5, 1, 50)
        max_tokens = _runtime_token_budget(args, 800)
        if hasattr(self.memory, "query"):
            items = self.memory.query(query, limit=limit)
        elif hasattr(self.memory, "get_relevant_memories"):
            raw_items = self.memory.get_relevant_memories(query)[:limit]
            items = [
                {
                    "text": getattr(item, "text", str(item)),
                    "scope": getattr(item, "scope", ""),
                    "priority": getattr(item, "priority", 0),
                    "tags": list(getattr(item, "tags", []) or []),
                }
                for item in raw_items
            ]
        else:
            return SafeRuntimeResult(False, "memory.query", error="Memory query API unavailable")

        budget_bytes = max_tokens * 4
        bounded = []
        used = 0
        for item in items:
            row = dict(item) if isinstance(item, dict) else {"text": str(item)}
            text = str(row.get("text", "") or "")
            overhead = sum(
                len(str(key).encode("utf-8")) + len(str(value).encode("utf-8"))
                for key, value in row.items()
                if key != "text"
            ) + 16
            available = max(0, budget_bytes - used - overhead)
            if bounded and available <= 32:
                break
            bounded_text = _truncate_utf8(text, available)
            row["text"] = bounded_text
            row["truncated"] = bounded_text != text
            bounded.append(row)
            used += overhead + len(bounded_text.encode("utf-8"))
            if used >= budget_bytes:
                break

        return SafeRuntimeResult(
            True,
            "memory.query",
            data=bounded,
            metadata={
                "max_tokens": max_tokens,
                "returned": len(bounded),
                "truncated": len(bounded) < len(items) or any(row.get("truncated") for row in bounded),
            },
        )

    def _op_session_search(self, args):
        if not self.db:
            return SafeRuntimeResult(False, "session.search", error="History database not attached")
        query = str(args.get("query", "")).strip()
        if not query:
            return SafeRuntimeResult(False, "session.search", error="query required")

        limit = _runtime_int(args.get("limit", 10), 10, 1, 50)
        offset = _runtime_int(args.get("offset", 0), 0, 0, 10_000)
        max_tokens = _runtime_token_budget(args, 800)

        from kitt.history.search_index import HistorySearchIndex

        search = HistorySearchIndex(self.db)
        rows = search.search(
            self.workspace_id,
            query,
            limit=limit,
            offset=offset,
        )
        budget_bytes = max_tokens * 4
        bounded: list[dict[str, Any]] = []
        used = 0
        omitted_bytes = 0
        allowed_keys = (
            "id",
            "title",
            "updated_at",
            "match_source",
            "match_role",
            "match_snippet",
            "search_score",
            "search_backend",
            "indexed_rowid",
        )
        for source in rows:
            row = {key: source.get(key) for key in allowed_keys if key in source}
            snippet = str(row.get("match_snippet", "") or "")
            fixed = {key: value for key, value in row.items() if key != "match_snippet"}
            overhead = sum(
                len(str(key).encode("utf-8")) + len(str(value).encode("utf-8"))
                for key, value in fixed.items()
            ) + 32
            available = max(0, budget_bytes - used - overhead)
            if bounded and available <= 32:
                omitted_bytes += len(snippet.encode("utf-8")) + overhead
                continue
            bounded_snippet = _truncate_utf8(snippet, available)
            row["match_snippet"] = bounded_snippet
            row["truncated"] = bounded_snippet != snippet
            bounded.append(row)
            used += overhead + len(bounded_snippet.encode("utf-8"))
            omitted_bytes += max(
                0,
                len(snippet.encode("utf-8")) - len(bounded_snippet.encode("utf-8")),
            )
            if used >= budget_bytes:
                break

        backend = (
            str(rows[0].get("search_backend"))
            if rows
            else search.status().backend
        )
        truncated = len(bounded) < len(rows) or any(row.get("truncated") for row in bounded)
        return SafeRuntimeResult(
            True,
            "session.search",
            data=bounded,
            context_handles=[f"ctx:sessions:{query[:64]}"],
            tokens_saved=omitted_bytes // 4,
            metadata={
                "backend": backend,
                "returned": len(bounded),
                "matched": len(rows),
                "offset": offset,
                "max_tokens": max_tokens,
                "truncated": truncated,
                "workspace_scoped": True,
            },
        )

    def _op_harness_refine_prepare(self, args):
        if self.refiner is None:
            return SafeRuntimeResult(
                False,
                "harness.refine.prepare",
                error="Harness refiner not attached",
            )
        proposal = args.get("proposal")
        if not isinstance(proposal, dict):
            return SafeRuntimeResult(
                False, "harness.refine.prepare", error="proposal object required"
            )
        refinement_id, preview = self.refiner.prepare(
            proposal,
            workspace_id=self.workspace_id,
            conversation_id=self.conversation_id,
        )
        return SafeRuntimeResult(
            True,
            "harness.refine.prepare",
            data={"id": refinement_id, "preview": preview},
        )

    def _op_harness_refine_apply(self, args):
        if self.refiner is None:
            return SafeRuntimeResult(
                False, "harness.refine.apply", error="Harness refiner not attached"
            )
        refinement_id = str(args.get("id") or "").strip()
        if not refinement_id:
            return SafeRuntimeResult(
                False, "harness.refine.apply", error="id required"
            )
        return SafeRuntimeResult(
            True,
            "harness.refine.apply",
            data=self.refiner.apply(refinement_id),
        )

    def _op_harness_refine_rollback(self, args):
        if self.refiner is None:
            return SafeRuntimeResult(
                False, "harness.refine.rollback", error="Harness refiner not attached"
            )
        refinement_id = str(args.get("id") or "").strip()
        if not refinement_id:
            return SafeRuntimeResult(
                False, "harness.refine.rollback", error="id required"
            )
        return SafeRuntimeResult(
            True,
            "harness.refine.rollback",
            data={"id": refinement_id, "rolled_back": self.refiner.rollback(refinement_id)},
        )

    def _op_skill_call(self, args, security_context):
        skill_name = args.get("name") or args.get("skill_name")
        if not skill_name:
            return SafeRuntimeResult(False, "skill.call", error="Skill name required")
        if not self.skills or not hasattr(self.skills, "execute_skill"):
            return SafeRuntimeResult(False, "skill.call", error=f"Executable skill '{skill_name}' not available")
        result = self.skills.execute_skill(
            skill_name,
            args.get("arguments", {}),
            runtime=self,
            security_context=security_context,
        )
        return SafeRuntimeResult(
            getattr(result, "success", True),
            "skill.call",
            data=getattr(result, "data", str(result)),
            error=getattr(result, "error", None),
        )

    def _op_mcp_call(
        self, args, turn_id, security_context, automatic_budget_reserved: bool = False
    ):
        tool_name = str(args.get("tool_name", ""))
        if not tool_name:
            return SafeRuntimeResult(False, "mcp.call", error="MCP tool_name required")
        if not self.registry:
            return SafeRuntimeResult(False, "mcp.call", error="Tool registry not attached")
        result = self.registry.execute_tool(
            tool_name,
            args.get("arguments", {}),
            turn_id=turn_id,
            conversation_id=self.conversation_id,
            workspace_id=self.workspace_id,
            origin="SAFE_RUNTIME_BROKER",
            security_context=security_context,
            automatic_budget_reserved=automatic_budget_reserved,
        )
        return SafeRuntimeResult(
            result.success,
            "mcp.call",
            data=result.output,
            error=result.error,
            metadata=dict(getattr(result, "metadata", {}) or {}),
        )

    def _op_state_get(self, args):
        key = str(args.get("key", ""))
        if not key:
            return SafeRuntimeResult(False, "state.get", error="key required")
        if not self.state:
            return SafeRuntimeResult(False, "state.get", error="State store not initialized")
        return SafeRuntimeResult(True, "state.get", data=self.state.get(key))

    def _op_state_set(self, args):
        key = str(args.get("key", ""))
        if not key:
            return SafeRuntimeResult(False, "state.set", error="key required")
        if not self.state:
            return SafeRuntimeResult(False, "state.set", error="State store not initialized")
        self.state.set(key, args.get("value"), ttl_seconds=args.get("ttl_seconds"))
        return SafeRuntimeResult(True, "state.set", data={"key": key, "status": "stored"})

    def _op_state_list(self):
        if not self.state:
            return SafeRuntimeResult(False, "state.list", error="State store not initialized")
        return SafeRuntimeResult(True, "state.list", data=self.state.list_keys())

    def _op_handles_resolve(self, args, security_context):
        handle = str(args.get("handle", ""))
        if not handle:
            return SafeRuntimeResult(False, "handles.resolve", error="handle required")
        resolved = self.handles.resolve(handle, security_context=security_context)
        return SafeRuntimeResult(True, "handles.resolve", data=resolved)
