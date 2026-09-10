from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from kitt.context_engine.context_map import ContextMapBuilder
from kitt.runtime.handles import ContextHandleResolver
from kitt.runtime.programmatic_flow import ProgrammaticToolFlow
from kitt.runtime.progressive import apply_progressive_search_view
from kitt.runtime.retrieval_guard import RetrievalGuard
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
    CAP_MEMORY_WRITE,
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


OPERATION_SPECS: Dict[str, RuntimeOperationSpec] = {
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
    "repo.edit_symbol": RuntimeOperationSpec("repo.edit_symbol", CAP_REPO_WRITE, "write_file", sensitive=True),
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
    "session.search": RuntimeOperationSpec(
        "session.search", CAP_MEMORY_READ, sensitive=False
    ),
    "memory.correct": RuntimeOperationSpec("memory.correct", CAP_MEMORY_WRITE, "memory_save", sensitive=True),
    "memory.concept": RuntimeOperationSpec("memory.concept", CAP_MEMORY_WRITE, "memory_save", sensitive=True),
    "memory.link": RuntimeOperationSpec("memory.link", CAP_MEMORY_WRITE, "memory_save", sensitive=True),
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
        self.retrieval_guard = RetrievalGuard()
        self.programmatic_flow = ProgrammaticToolFlow(self)
        self.context_map_builder = ContextMapBuilder(
            self.index, self.goals, self.registry
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
                capabilities,
                delegated_grant,
                delegated_approval_id,
            )
            if op == "repo.search":
                result = apply_progressive_search_view(result, args)
            if op in {"repo.read", "repo.search"}:
                result = self.retrieval_guard.observe(op, args, result)
            elif result.success and op in {"repo.edit_symbol", "patch.apply"}:
                self.retrieval_guard.invalidate()
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
    ) -> SafeRuntimeResult:
        handlers = {
            "repo.read": lambda: self._op_repo_read(args, turn_id, origin, security_context),
            "repo.search": lambda: self._op_repo_search(args, turn_id, origin, security_context),
            "repo.inspect_symbol": lambda: self._op_repo_inspect_symbol(args, turn_id, origin, security_context),
            "repo.read_symbol": lambda: self._op_repo_read_symbol(args, security_context),
            "repo.references": lambda: self._op_repo_references(args, security_context),
            "repo.context_map": lambda: self._op_repo_context_map(
                args, security_context
            ),
            "flow.execute": lambda: self._op_flow_execute(
                args, turn_id, origin, capabilities, security_context
            ),
            "repo.edit_symbol": lambda: self._op_repo_edit_symbol(args, turn_id, security_context),
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
            "session.search": lambda: self._op_session_search(args),
            "memory.correct": lambda: self._op_memory_correct(args),
            "memory.concept": lambda: self._op_memory_concept(args),
            "memory.link": lambda: self._op_memory_link(args),
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

    def _resolve_native_symbol(self, value: str):
        engine = getattr(self.registry, "native_engine", None) if self.registry else None
        if engine is None:
            return None, None
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
        engine, found = self._resolve_native_symbol(value)
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
            metadata={"max_tokens": max_tokens, "truncated": bounded.get("truncated", False)},
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
                "truncated": len(bounded) < len(allowed),
            },
        )

    def _canonicalize_repo_edit_args(self, args: dict, security_context) -> dict:
        value = str(args.get("symbol_id", args.get("symbol", ""))).strip()
        replacement = args.get("replacement")
        if not value or not isinstance(replacement, str):
            raise ValueError("symbol_id/symbol and replacement are required")
        engine, found = self._resolve_native_symbol(value)
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
        if expected_path and str(symbol["path"]) != expected_path:
            return SafeRuntimeResult(False, "repo.edit_symbol", error="structural edit target path changed after approval")
        self._assert_native_path_allowed(security_context, symbol["path"])
        coordinator = getattr(self.registry, "coordinator", None) if self.registry else None
        if coordinator is not None and security_context is not None and getattr(security_context, "principal_type", "") == "CHILD":
            coordinator.claim_symbol_for_edit(
                symbol_id, security_context.principal_id,
                str(args.get("intent", f"edit {symbol_id}")),
            )
        result = engine.replace_symbol(
            symbol_id, replacement, expected_hash=expected_hash,
            validate_syntax=bool(args.get("validate_syntax", True)),
        )
        if self.registry and result.get("changed"):
            self.registry._refresh_index([result["path"]])
            recorder = getattr(self.registry, "record_changed_paths", None)
            if recorder is not None:
                recorder(
                    conversation_id=self.conversation_id, turn_id=turn_id,
                    changed=[result["path"]], kind="native_symbol_edit",
                )
        return SafeRuntimeResult(True, "repo.edit_symbol", data=result)

    def _op_memory_correct(self, args):
        if not self.memory or not hasattr(self.memory, "remember_correction"):
            return SafeRuntimeResult(False, "memory.correct", error="hybrid memory service unavailable")
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
