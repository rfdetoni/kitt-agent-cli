from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from kitt.runtime.handles import ContextHandleResolver
from kitt.runtime.state import RuntimeStateStore
from kitt.security.capabilities import (
    ALL_CAPABILITIES,
    CAP_ARTIFACT_READ,
    CAP_ARTIFACT_WRITE,
    CAP_CHILD_MESSAGE,
    CAP_CHILD_SPAWN,
    CAP_GOAL_MANAGE,
    CAP_MCP_CALL,
    CAP_MEMORY_READ,
    CAP_MEMORY_WRITE,
    CAP_PROCESS_RUN,
    CAP_REPO_READ,
    CAP_REPO_SEARCH,
    CAP_REPO_WRITE,
)


@dataclass
class SafeRuntimeResult:
    success: bool
    operation: str
    data: Any = None
    error: Optional[str] = None
    context_handles: List[str] = field(default_factory=list)
    tokens_saved: int = 0
    duration_ms: float = 0.0


class SafeRuntime:
    """Persistent, programmable safe runtime for compact, deterministic operations."""

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
        )

    def execute(
        self,
        operation: str,
        arguments: Optional[Dict[str, Any]] = None,
        turn_id: str = "runtime_turn",
        origin: str = "MODEL",
    ) -> SafeRuntimeResult:
        """Execute a typed, policy-governed runtime operation."""
        start = time.perf_counter()
        args = arguments or {}
        op = operation.strip() if operation else ""

        try:
            if op == "repo.read":
                res = self._op_repo_read(args, turn_id, origin)
            elif op == "repo.search":
                res = self._op_repo_search(args, turn_id, origin)
            elif op == "repo.inspect_symbol":
                res = self._op_repo_inspect_symbol(args, turn_id, origin)
            elif op == "artifacts.store":
                res = self._op_artifacts_store(args, turn_id, origin)
            elif op == "artifacts.read":
                res = self._op_artifacts_read(args, turn_id, origin)
            elif op == "patch.apply":
                res = self._op_patch_apply(args, turn_id, origin)
            elif op == "process.run":
                res = self._op_process_run(args, turn_id, origin)
            elif op == "children.spawn":
                res = self._op_children_spawn(args, turn_id, origin)
            elif op == "children.send":
                res = self._op_children_send(args, turn_id, origin)
            elif op == "children.inspect":
                res = self._op_children_inspect(args, turn_id, origin)
            elif op == "goal.inspect":
                res = self._op_goal_inspect(args, turn_id, origin)
            elif op == "goal.update":
                res = self._op_goal_update(args, turn_id, origin)
            elif op == "memory.query":
                res = self._op_memory_query(args, turn_id, origin)
            elif op == "skill.call":
                res = self._op_skill_call(args, turn_id, origin)
            elif op == "mcp.call":
                res = self._op_mcp_call(args, turn_id, origin)
            elif op == "state.get":
                res = self._op_state_get(args)
            elif op == "state.set":
                res = self._op_state_set(args)
            elif op == "state.list":
                res = self._op_state_list(args)
            elif op == "handles.resolve":
                res = self._op_handles_resolve(args)
            else:
                return SafeRuntimeResult(
                    success=False,
                    operation=op,
                    error=f"Unknown runtime operation: '{op}'",
                    duration_ms=(time.perf_counter() - start) * 1000,
                )

            res.duration_ms = (time.perf_counter() - start) * 1000
            return res
        except Exception as exc:
            return SafeRuntimeResult(
                success=False,
                operation=op,
                error=f"Runtime error in {op}: {exc}",
                duration_ms=(time.perf_counter() - start) * 1000,
            )

    # --- Operation handlers ---

    def _op_repo_read(self, args: dict, turn_id: str, origin: str) -> SafeRuntimeResult:
        if self.registry:
            tool_res = self.registry.execute_tool(
                "read_file", args, turn_id=turn_id,
                conversation_id=self.conversation_id, workspace_id=self.workspace_id,
                origin=origin
            )
            handle = f"ctx:file:{args.get('path', '')}:{args.get('start_line', 1)}-{args.get('end_line', 100)}"
            return SafeRuntimeResult(
                success=tool_res.success,
                operation="repo.read",
                data=tool_res.output,
                error=tool_res.error,
                context_handles=[handle] if tool_res.success else [],
            )
        return SafeRuntimeResult(success=False, operation="repo.read", error="No tool registry attached")

    def _op_repo_search(self, args: dict, turn_id: str, origin: str) -> SafeRuntimeResult:
        if self.registry:
            tool_res = self.registry.execute_tool(
                "search", args, turn_id=turn_id,
                conversation_id=self.conversation_id, workspace_id=self.workspace_id,
                origin=origin
            )
            return SafeRuntimeResult(
                success=tool_res.success,
                operation="repo.search",
                data=tool_res.output,
                error=tool_res.error,
            )
        return SafeRuntimeResult(success=False, operation="repo.search", error="No tool registry attached")

    def _op_repo_inspect_symbol(self, args: dict, turn_id: str, origin: str) -> SafeRuntimeResult:
        symbol = args.get("symbol", "").strip()
        if not symbol:
            return SafeRuntimeResult(success=False, operation="repo.inspect_symbol", error="Symbol argument required")

        handle_info = self.handles.resolve(f"ctx:repo:{symbol}")
        symbols = handle_info.get("symbols", [])
        snippets = []

        for sym in symbols[:3]:
            rel_path = sym.get("path", "")
            start = max(1, sym.get("start_line", 1) - 5)
            end = sym.get("end_line", start + 30) + 5
            read_args = {"path": rel_path, "start_line": start, "end_line": end}
            read_res = self._op_repo_read(read_args, turn_id, origin)
            if read_res.success:
                snippets.append({
                    "symbol": sym.get("name", symbol),
                    "path": rel_path,
                    "kind": sym.get("kind", ""),
                    "lines": f"{start}-{end}",
                    "content": read_res.data,
                })

        return SafeRuntimeResult(
            success=True,
            operation="repo.inspect_symbol",
            data={"symbol": symbol, "matches": symbols, "snippets": snippets},
            context_handles=[f"ctx:repo:{symbol}"],
            tokens_saved=350,  # saved multiple roundtrips
        )

    def _op_artifacts_store(self, args: dict, turn_id: str, origin: str) -> SafeRuntimeResult:
        if self.registry:
            tool_res = self.registry.execute_tool(
                "artifact_store", args, turn_id=turn_id,
                conversation_id=self.conversation_id, workspace_id=self.workspace_id,
                origin=origin
            )
            art_id = tool_res.metadata.get("artifact_id", "") if hasattr(tool_res, "metadata") else ""
            handles = [f"artifact:{art_id}"] if art_id else []
            return SafeRuntimeResult(
                success=tool_res.success,
                operation="artifacts.store",
                data=tool_res.output,
                error=tool_res.error,
                context_handles=handles,
            )
        return SafeRuntimeResult(success=False, operation="artifacts.store", error="No tool registry attached")

    def _op_artifacts_read(self, args: dict, turn_id: str, origin: str) -> SafeRuntimeResult:
        if self.registry:
            tool_res = self.registry.execute_tool(
                "artifact_read", args, turn_id=turn_id,
                conversation_id=self.conversation_id, workspace_id=self.workspace_id,
                origin=origin
            )
            art_id = args.get("artifact_id", "")
            return SafeRuntimeResult(
                success=tool_res.success,
                operation="artifacts.read",
                data=tool_res.output,
                error=tool_res.error,
                context_handles=[f"artifact:{art_id}"] if art_id else [],
            )
        return SafeRuntimeResult(success=False, operation="artifacts.read", error="No tool registry attached")

    def _op_patch_apply(self, args: dict, turn_id: str, origin: str) -> SafeRuntimeResult:
        if self.registry:
            tool_res = self.registry.execute_tool(
                "apply_patch", args, turn_id=turn_id,
                conversation_id=self.conversation_id, workspace_id=self.workspace_id,
                origin=origin
            )
            return SafeRuntimeResult(
                success=tool_res.success,
                operation="patch.apply",
                data=tool_res.output,
                error=tool_res.error,
            )
        return SafeRuntimeResult(success=False, operation="patch.apply", error="No tool registry attached")

    def _op_process_run(self, args: dict, turn_id: str, origin: str) -> SafeRuntimeResult:
        if self.registry:
            tool_res = self.registry.execute_tool(
                "run_command", args, turn_id=turn_id,
                conversation_id=self.conversation_id, workspace_id=self.workspace_id,
                origin=origin
            )
            return SafeRuntimeResult(
                success=tool_res.success,
                operation="process.run",
                data=tool_res.output,
                error=tool_res.error,
            )
        return SafeRuntimeResult(success=False, operation="process.run", error="No tool registry attached")

    def _op_children_spawn(self, args: dict, turn_id: str, origin: str) -> SafeRuntimeResult:
        if self.registry:
            tool_res = self.registry.execute_tool(
                "child_spawn", args, turn_id=turn_id,
                conversation_id=self.conversation_id, workspace_id=self.workspace_id,
                origin=origin
            )
            child_id = tool_res.metadata.get("child_id", "") if hasattr(tool_res, "metadata") else ""
            return SafeRuntimeResult(
                success=tool_res.success,
                operation="children.spawn",
                data=tool_res.output,
                error=tool_res.error,
                context_handles=[f"child:{child_id}"] if child_id else [],
            )
        return SafeRuntimeResult(success=False, operation="children.spawn", error="No tool registry attached")

    def _op_children_send(self, args: dict, turn_id: str, origin: str) -> SafeRuntimeResult:
        child_id = args.get("child_id", "")
        message = args.get("message", "")
        if not child_id or not message:
            return SafeRuntimeResult(success=False, operation="children.send", error="child_id and message required")

        if self.children and hasattr(self.children, "send_message"):
            msg_obj = self.children.send_message(
                conversation_id=self.conversation_id,
                parent_id=self.conversation_id,
                child_id=child_id,
                sender_id=self.conversation_id,
                recipient_id=child_id,
                payload=message if isinstance(message, dict) else {"text": str(message)},
            )
            return SafeRuntimeResult(
                success=True,
                operation="children.send",
                data={"message_id": getattr(msg_obj, "id", ""), "status": "SENT"},
                context_handles=[f"child:{child_id}"],
            )
        return SafeRuntimeResult(success=False, operation="children.send", error="Child messaging not available")

    def _op_children_inspect(self, args: dict, turn_id: str, origin: str) -> SafeRuntimeResult:
        child_id = args.get("child_id", "")
        if not child_id:
            return SafeRuntimeResult(success=False, operation="children.inspect", error="child_id required")
        if self.children:
            child = self.children.inspect(child_id)
            if not child:
                return SafeRuntimeResult(success=False, operation="children.inspect", error=f"Child {child_id} not found")
            return SafeRuntimeResult(
                success=True,
                operation="children.inspect",
                data={
                    "id": getattr(child, "id", child_id),
                    "name": getattr(child, "name", ""),
                    "state": getattr(child, "state", ""),
                    "task": getattr(child, "task", ""),
                    "result_artifact_id": getattr(child, "result_artifact_id", None),
                    "error": getattr(child, "error", None),
                },
                context_handles=[f"child:{child_id}"],
            )
        return SafeRuntimeResult(success=False, operation="children.inspect", error="Child manager not attached")

    def _op_goal_inspect(self, args: dict, turn_id: str, origin: str) -> SafeRuntimeResult:
        goal_id = args.get("goal_id", "")
        if not goal_id:
            return SafeRuntimeResult(success=False, operation="goal.inspect", error="goal_id required")
        if self.goals:
            goal = self.goals.get(goal_id)
            if not goal:
                return SafeRuntimeResult(success=False, operation="goal.inspect", error=f"Goal {goal_id} not found")
            return SafeRuntimeResult(
                success=True,
                operation="goal.inspect",
                data={
                    "id": getattr(goal, "id", goal_id),
                    "objective": getattr(goal, "objective", ""),
                    "state": getattr(goal, "state", ""),
                    "turns_used": getattr(goal, "turns_used", 0),
                    "tokens_used": getattr(goal, "tokens_used", 0),
                    "success_criteria": getattr(goal, "success_criteria", []),
                },
                context_handles=[f"goal:{goal_id}"],
            )
        return SafeRuntimeResult(success=False, operation="goal.inspect", error="Goal service not attached")

    def _op_goal_update(self, args: dict, turn_id: str, origin: str) -> SafeRuntimeResult:
        goal_id = args.get("goal_id", "")
        state = args.get("state", "")
        if not goal_id or not state:
            return SafeRuntimeResult(success=False, operation="goal.update", error="goal_id and state required")
        if self.goals:
            res = self.goals.update_state(goal_id, state, last_error=args.get("last_error"))
            return SafeRuntimeResult(
                success=bool(res),
                operation="goal.update",
                data={"goal_id": goal_id, "state": state},
                context_handles=[f"goal:{goal_id}"],
            )
        return SafeRuntimeResult(success=False, operation="goal.update", error="Goal service not attached")

    def _op_memory_query(self, args: dict, turn_id: str, origin: str) -> SafeRuntimeResult:
        query = args.get("query", "")
        if self.memory:
            items = self.memory.query(query, limit=args.get("limit", 5))
            return SafeRuntimeResult(
                success=True,
                operation="memory.query",
                data=items,
            )
        return SafeRuntimeResult(success=False, operation="memory.query", error="Memory service not attached")

    def _op_skill_call(self, args: dict, turn_id: str, origin: str) -> SafeRuntimeResult:
        skill_name = args.get("name") or args.get("skill_name")
        skill_args = args.get("arguments", {})
        if not skill_name:
            return SafeRuntimeResult(success=False, operation="skill.call", error="Skill name required")
        if self.skills and hasattr(self.skills, "execute_skill"):
            res = self.skills.execute_skill(skill_name, skill_args, runtime=self)
            return SafeRuntimeResult(
                success=getattr(res, "success", True),
                operation="skill.call",
                data=getattr(res, "data", str(res)),
                error=getattr(res, "error", None),
            )
        return SafeRuntimeResult(success=False, operation="skill.call", error=f"Executable skill '{skill_name}' not available")

    def _op_mcp_call(self, args: dict, turn_id: str, origin: str) -> SafeRuntimeResult:
        tool_name = args.get("tool_name", "")
        tool_args = args.get("arguments", {})
        if not tool_name:
            return SafeRuntimeResult(success=False, operation="mcp.call", error="MCP tool_name required")
        if self.registry:
            tool_res = self.registry.execute_tool(
                tool_name, tool_args, turn_id=turn_id,
                conversation_id=self.conversation_id, workspace_id=self.workspace_id,
                origin=origin
            )
            return SafeRuntimeResult(
                success=tool_res.success,
                operation="mcp.call",
                data=tool_res.output,
                error=tool_res.error,
            )
        return SafeRuntimeResult(success=False, operation="mcp.call", error="Tool registry not attached")

    def _op_state_get(self, args: dict) -> SafeRuntimeResult:
        key = args.get("key", "")
        if not key:
            return SafeRuntimeResult(success=False, operation="state.get", error="key required")
        if not self.state:
            return SafeRuntimeResult(success=False, operation="state.get", error="State store not initialized")
        val = self.state.get(key)
        return SafeRuntimeResult(success=True, operation="state.get", data=val)

    def _op_state_set(self, args: dict) -> SafeRuntimeResult:
        key = args.get("key", "")
        value = args.get("value")
        ttl = args.get("ttl_seconds")
        if not key:
            return SafeRuntimeResult(success=False, operation="state.set", error="key required")
        if not self.state:
            return SafeRuntimeResult(success=False, operation="state.set", error="State store not initialized")
        self.state.set(key, value, ttl_seconds=ttl)
        return SafeRuntimeResult(success=True, operation="state.set", data={"key": key, "status": "stored"})

    def _op_state_list(self, args: dict) -> SafeRuntimeResult:
        if not self.state:
            return SafeRuntimeResult(success=False, operation="state.list", error="State store not initialized")
        keys = self.state.list_keys()
        return SafeRuntimeResult(success=True, operation="state.list", data=keys)

    def _op_handles_resolve(self, args: dict) -> SafeRuntimeResult:
        handle = args.get("handle", "")
        if not handle:
            return SafeRuntimeResult(success=False, operation="handles.resolve", error="handle required")
        resolved = self.handles.resolve(handle)
        return SafeRuntimeResult(success=True, operation="handles.resolve", data=resolved)
