from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from kitt.runtime.handles import ContextHandleResolver
from kitt.runtime.state import RuntimeStateStore
from kitt.security.capabilities import (
    CAP_ARTIFACT_READ,
    CAP_ARTIFACT_WRITE,
    CAP_CHILD_INSPECT,
    CAP_CHILD_MESSAGE,
    CAP_CHILD_SPAWN,
    CAP_GOAL_MANAGE,
    CAP_MCP_CALL,
    CAP_MEMORY_READ,
    CAP_PROCESS_RUN,
    CAP_REPO_READ,
    CAP_REPO_SEARCH,
    CAP_REPO_WRITE,
)


@dataclass(frozen=True)
class RuntimeOperationSpec:
    name: str
    required_capability: Optional[str]
    policy_tool_action: Optional[str] = None
    sensitive: bool = False
    resume_tool_name: Optional[str] = None


OPERATION_SPECS: Dict[str, RuntimeOperationSpec] = {
    "repo.read": RuntimeOperationSpec("repo.read", CAP_REPO_READ, "read_file"),
    "repo.search": RuntimeOperationSpec("repo.search", CAP_REPO_SEARCH, "search"),
    "repo.inspect_symbol": RuntimeOperationSpec(
        "repo.inspect_symbol", CAP_REPO_READ, "read_file"
    ),
    "artifacts.store": RuntimeOperationSpec(
        "artifacts.store",
        CAP_ARTIFACT_WRITE,
        "artifact_store",
        sensitive=True,
        resume_tool_name="artifact_store",
    ),
    "artifacts.read": RuntimeOperationSpec(
        "artifacts.read", CAP_ARTIFACT_READ, "artifact_read"
    ),
    "patch.apply": RuntimeOperationSpec(
        "patch.apply",
        CAP_REPO_WRITE,
        "apply_patch",
        sensitive=True,
        resume_tool_name="apply_patch",
    ),
    "process.run": RuntimeOperationSpec(
        "process.run",
        CAP_PROCESS_RUN,
        "run_command",
        sensitive=True,
        resume_tool_name="run_command",
    ),
    "children.spawn": RuntimeOperationSpec(
        "children.spawn",
        CAP_CHILD_SPAWN,
        "child_spawn",
        sensitive=True,
        resume_tool_name="child_spawn",
    ),
    "children.send": RuntimeOperationSpec(
        "children.send", CAP_CHILD_MESSAGE, sensitive=False
    ),
    "children.inspect": RuntimeOperationSpec(
        "children.inspect", CAP_CHILD_INSPECT, sensitive=False
    ),
    "goal.inspect": RuntimeOperationSpec(
        "goal.inspect", CAP_GOAL_MANAGE, sensitive=False
    ),
    "goal.update": RuntimeOperationSpec(
        "goal.update", CAP_GOAL_MANAGE, "goal_update", sensitive=True
    ),
    "memory.query": RuntimeOperationSpec(
        "memory.query", CAP_MEMORY_READ, sensitive=False
    ),
    "skill.call": RuntimeOperationSpec("skill.call", CAP_REPO_READ, sensitive=False),
    "mcp.call": RuntimeOperationSpec(
        "mcp.call", CAP_MCP_CALL, "mcp_call", sensitive=True
    ),
    "state.get": RuntimeOperationSpec("state.get", CAP_REPO_READ, sensitive=False),
    "state.set": RuntimeOperationSpec("state.set", CAP_REPO_WRITE, sensitive=False),
    "state.list": RuntimeOperationSpec("state.list", CAP_REPO_READ, sensitive=False),
    "handles.resolve": RuntimeOperationSpec(
        "handles.resolve", None, sensitive=False
    ),
}


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
        spec = OPERATION_SPECS.get(op)
        if spec is None:
            return self._result(
                start,
                SafeRuntimeResult(False, op, error=f"Unknown runtime operation: '{op}'"),
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

        delegated_grant = None
        delegated_approval_id = None
        if self.registry and getattr(self.registry, "policy", None):
            policy = self.registry.policy
            if (
                getattr(getattr(policy, "autonomy", None), "level", None) == "read_only"
                and spec.sensitive
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

        try:
            result = self._dispatch(
                op,
                args,
                turn_id,
                origin,
                security_context,
                delegated_grant,
                delegated_approval_id,
            )
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
        grant,
        expected_approval_id,
    ) -> SafeRuntimeResult:
        handlers = {
            "repo.read": lambda: self._op_repo_read(args, turn_id, origin, security_context),
            "repo.search": lambda: self._op_repo_search(args, turn_id, origin, security_context),
            "repo.inspect_symbol": lambda: self._op_repo_inspect_symbol(args, turn_id, origin, security_context),
            "artifacts.store": lambda: self._op_registry_tool("artifacts.store", "artifact_store", args, turn_id, origin, security_context, grant, expected_approval_id),
            "artifacts.read": lambda: self._op_registry_tool("artifacts.read", "artifact_read", args, turn_id, origin, security_context),
            "patch.apply": lambda: self._op_registry_tool("patch.apply", "apply_patch", args, turn_id, origin, security_context, grant, expected_approval_id),
            "process.run": lambda: self._op_registry_tool("process.run", "run_command", args, turn_id, origin, security_context, grant, expected_approval_id),
            "children.spawn": lambda: self._op_registry_tool("children.spawn", "child_spawn", args, turn_id, origin, security_context, grant, expected_approval_id),
            "children.send": lambda: self._op_children_send(args),
            "children.inspect": lambda: self._op_children_inspect(args),
            "goal.inspect": lambda: self._op_goal_inspect(args),
            "goal.update": lambda: self._op_goal_update(args),
            "memory.query": lambda: self._op_memory_query(args),
            "skill.call": lambda: self._op_skill_call(args, security_context),
            "mcp.call": lambda: self._op_mcp_call(args, turn_id, security_context),
            "state.get": lambda: self._op_state_get(args),
            "state.set": lambda: self._op_state_set(args),
            "state.list": lambda: self._op_state_list(),
            "handles.resolve": lambda: self._op_handles_resolve(args, security_context),
        }
        return handlers[op]()

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
        )
        metadata = dict(getattr(tool_result, "metadata", {}) or {})
        handles: list[str] = []
        if tool_name == "artifact_store" and metadata.get("artifact_id"):
            handles.append(f"artifact:{metadata['artifact_id']}")
        if tool_name == "child_spawn" and metadata.get("child_id"):
            handles.append(f"child:{metadata['child_id']}")
        return SafeRuntimeResult(
            success=tool_result.success,
            operation=operation,
            data=tool_result.output,
            error=tool_result.error,
            context_handles=handles,
            metadata={"effective_tool_name": tool_name, **metadata},
        )

    def _op_repo_read(self, args, turn_id, origin, security_context):
        result = self._op_registry_tool(
            "repo.read", "read_file", args, turn_id, origin, security_context
        )
        if result.success:
            result.context_handles = [
                f"ctx:file:{args.get('path', '')}:{args.get('start_line', 1)}-{args.get('end_line', 100)}"
            ]
        return result

    def _op_repo_search(self, args, turn_id, origin, security_context):
        return self._op_registry_tool(
            "repo.search", "search", args, turn_id, origin, security_context
        )

    def _op_repo_inspect_symbol(self, args, turn_id, origin, security_context):
        symbol = str(args.get("symbol", "")).strip()
        if not symbol:
            return SafeRuntimeResult(False, "repo.inspect_symbol", error="Symbol argument required")
        handle_info = self.handles.resolve(
            f"ctx:repo:{symbol}", security_context=security_context
        )
        symbols = handle_info.get("symbols", [])
        snippets = []
        for item in symbols[:3]:
            path = item.get("path", "")
            start = max(1, int(item.get("start_line", 1)) - 5)
            end = int(item.get("end_line", start + 30)) + 5
            read_result = self._op_repo_read(
                {"path": path, "start_line": start, "end_line": end},
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
        content_chars = sum(len(str(item.get("content", ""))) for item in snippets)
        return SafeRuntimeResult(
            True,
            "repo.inspect_symbol",
            data={"symbol": symbol, "matches": symbols, "snippets": snippets},
            context_handles=[f"ctx:repo:{symbol}"],
            tokens_saved=max(50, content_chars // 4),
        )

    def _op_children_send(self, args):
        child_id = str(args.get("child_id", ""))
        message = args.get("message", "")
        if not child_id or not message:
            return SafeRuntimeResult(False, "children.send", error="child_id and message required")
        if not self.children or not hasattr(self.children, "send_message"):
            return SafeRuntimeResult(False, "children.send", error="Child messaging not available")
        message_object = self.children.send_message(
            conversation_id=self.conversation_id,
            parent_id=self.conversation_id,
            child_id=child_id,
            sender_id=self.conversation_id,
            recipient_id=child_id,
            payload=message if isinstance(message, dict) else {"text": str(message)},
        )
        return SafeRuntimeResult(
            True,
            "children.send",
            data={"message_id": getattr(message_object, "id", ""), "status": "SENT"},
            context_handles=[f"child:{child_id}"],
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

    def _op_memory_query(self, args):
        if not self.memory:
            return SafeRuntimeResult(False, "memory.query", error="Memory service not attached")
        query = str(args.get("query", ""))
        limit = max(1, min(int(args.get("limit", 5)), 50))
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
        return SafeRuntimeResult(True, "memory.query", data=items)

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

    def _op_mcp_call(self, args, turn_id, security_context):
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
