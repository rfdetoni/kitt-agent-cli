from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from kitt.context_engine.engine import ContextEngine
from kitt.edit_format.applier import DiffApplier
from kitt.edit_format.parser import SearchReplaceParser
from kitt.index.repository import RepositoryIndex
from kitt.tools.approval import ApprovalGrant, ApprovalManager
from kitt.tools.artifact_tools import ArtifactTools
from kitt.tools.child_tools import ChildTools
from kitt.tools.goal_tools import GoalTools
from kitt.tools.handlers import ToolContext, ToolHandler
from kitt.tools.handlers.files import ListFilesHandler, ReadFileHandler, WriteFileHandler
from kitt.tools.handlers.safe_runtime import SafeRuntimeHandler
from kitt.tools.handlers.search import RepositoryMapHandler, SearchHandler
from kitt.tools.handlers.services import (
    ArtifactListHandler,
    ArtifactReadHandler,
    ArtifactStoreHandler,
    ChildSpawnHandler,
    GoalAddGateHandler,
    GoalCreateHandler,
    HarnessRememberHandler,
    QueueInputHandler,
)
from kitt.tools.handlers.system import (
    ApplyPatchHandler,
    GitDiffHandler,
    GitStatusHandler,
    PythonComputeHandler,
    RunCommandHandler,
)
from kitt.tools.path_policy import WorkspacePathPolicy
from kitt.tools.policy_engine import PolicyEngine
from kitt.tools.process_runner import ProcessRunner
from kitt.tools.safe_python import SafePythonExecutor


@dataclass
class ToolResult:
    success: bool
    output: str
    error: Optional[str] = None
    bytes_count: int = 0
    truncated: bool = False
    requires_approval: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


class ToolRegistry:
    """Executable tool registry and the canonical security boundary for tools."""

    def __init__(self, root_dir: str = ".", context_engine: ContextEngine | None = None):
        self.root_path = Path(root_dir).resolve()
        self.policy = PolicyEngine(root_dir=root_dir)
        self.path_policy = WorkspacePathPolicy(root_dir=root_dir)
        self.applier = DiffApplier()
        self.parser = SearchReplaceParser()
        self.approval_manager = ApprovalManager()
        self.safe_python = SafePythonExecutor()
        self.process_runner = ProcessRunner(root_dir)
        self.context_engine = context_engine or ContextEngine(
            repository_index=RepositoryIndex(self.root_path)
        )

        self.artifacts = None
        self.artifact_tools = None
        self.queue_service = None
        self.goal_service = None
        self.goal_tools = None
        self.child_manager = None
        self.child_tools = None
        self.harness_service = None
        self.memory_service = None
        self.skill_manager = None
        self.db = None
        self.event_bus = None
        self._processor = None

        self._custom_tools: Dict[str, Dict[str, Any]] = {}
        self._safe_runtime_instance = None
        self.runtime_mode = "auto"
        self._handlers: Dict[str, ToolHandler] = {
            "kitt_runtime": SafeRuntimeHandler(),
            "list_files": ListFilesHandler(),
            "read_file": ReadFileHandler(),
            "write_file": WriteFileHandler(),
            "search": SearchHandler(),
            "repository_map": RepositoryMapHandler(),
            "python_compute": PythonComputeHandler(),
            "apply_patch": ApplyPatchHandler(),
            "run_command": RunCommandHandler(),
            "git_status": GitStatusHandler(),
            "git_diff": GitDiffHandler(),
            "artifact_store": ArtifactStoreHandler(),
            "artifact_read": ArtifactReadHandler(),
            "artifact_list": ArtifactListHandler(),
            "queue_input": QueueInputHandler(),
            "goal_create": GoalCreateHandler(),
            "goal_add_gate": GoalAddGateHandler(),
            "child_spawn": ChildSpawnHandler(),
            "harness_remember": HarnessRememberHandler(),
        }

    def register(
        self,
        tool_name: str,
        handler: Any,
        description: str = "",
        schema: Optional[Dict[str, Any]] = None,
        owner_plugin_id: Optional[str] = None,
        *,
        scope_aware: bool = False,
    ) -> None:
        """Register a dynamic plugin/MCP tool.

        Dynamic tools are not considered path-scope aware by default. This is
        intentionally fail-closed: a path-restricted child cannot escape its
        boundary through an opaque plugin or MCP implementation.
        """

        class _CustomHandler:
            def __init__(self, function):
                self.function = function

            def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
                if hasattr(self.function, "execute"):
                    result = self.function.execute(args)
                else:
                    result = self.function(args)
                if isinstance(result, ToolResult):
                    return result
                return ToolResult(success=True, output=str(result))

        wrapped_handler = (
            handler
            if (
                hasattr(handler, "execute")
                and callable(getattr(handler, "execute"))
                and not callable(handler)
            )
            else _CustomHandler(handler)
        )
        self._handlers[tool_name] = wrapped_handler
        self._custom_tools[tool_name] = {
            "name": tool_name,
            "description": description or f"Custom tool {tool_name}",
            "args": schema or {},
            "owner": owner_plugin_id,
            "scope_aware": bool(scope_aware),
        }
        if hasattr(self.policy, "allow_custom_tool"):
            self.policy.allow_custom_tool(tool_name)

    def unregister_by_owner(self, owner_plugin_id: str) -> int:
        to_remove = [
            name
            for name, metadata in self._custom_tools.items()
            if metadata.get("owner") == owner_plugin_id
        ]
        for name in to_remove:
            self._handlers.pop(name, None)
            self._custom_tools.pop(name, None)
            if hasattr(self.policy, "disallow_custom_tool"):
                self.policy.disallow_custom_tool(name)
        return len(to_remove)

    @property
    def repository_index(self):
        return getattr(self.context_engine, "index", None)

    def _refresh_index(self, paths: Optional[List[str]] = None) -> None:
        index = self.repository_index
        if index is None:
            return
        if paths and hasattr(index, "update_paths"):
            index.update_paths(paths)
        elif index.index_generation() == 0:
            index.build_or_update()

    def attach_services(
        self,
        artifacts=None,
        queue_service=None,
        goal_service=None,
        child_manager=None,
        harness_service=None,
        memory_service=None,
        skill_manager=None,
        db=None,
    ) -> None:
        self.artifacts = artifacts
        self.artifact_tools = ArtifactTools(artifacts) if artifacts else None
        self.queue_service = queue_service
        self.goal_service = goal_service
        self.goal_tools = GoalTools(goal_service) if goal_service else None
        self.child_manager = child_manager
        self.child_tools = ChildTools(child_manager) if child_manager else None
        self.harness_service = harness_service
        self.memory_service = memory_service
        self.skill_manager = skill_manager
        self.db = db

    def attach_processor(self, processor) -> None:
        """Attach application-level edit observers after TurnProcessor is built."""
        self._processor = processor

    def record_edit_result(
        self,
        *,
        conversation_id: str,
        turn_id: str,
        edit_result,
        kind: str,
    ) -> None:
        """Record edits performed behind composite tool surfaces exactly once."""
        if not edit_result or not getattr(edit_result, "success", False):
            return
        changed = list(edit_result.applied_files + edit_result.created_files)
        processor = self._processor
        if processor is not None:
            processor.session_state.last_changeset = edit_result.changeset
            processor.working_set.touch_paths(
                conversation_id,
                changed,
                turn_id,
                weight=2.0,
                kind=kind,
            )
            processor._emit(
                "EditApplied",
                {"applied": edit_result.applied_files, "created": edit_result.created_files},
            )
        elif self.event_bus is not None:
            self.event_bus.publish(
                "EditApplied",
                {"applied": edit_result.applied_files, "created": edit_result.created_files},
            )

    def record_changed_paths(
        self, *, conversation_id: str, turn_id: str, changed: List[str], kind: str
    ) -> None:
        changed = [str(path) for path in changed if str(path)]
        if not changed:
            return
        processor = self._processor
        if processor is not None:
            processor.working_set.touch_paths(
                conversation_id, changed, turn_id, weight=2.0, kind=kind
            )
            processor._emit("EditApplied", {"applied": changed, "created": [], "kind": kind})
        elif self.event_bus is not None:
            self.event_bus.publish(
                "EditApplied", {"applied": changed, "created": [], "kind": kind}
            )

    def get_tool_definitions(
        self, enabled_tools: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        all_tools = [
            {
                "name": "kitt_runtime",
                "description": (
                    "Execute safe, compact, policy-governed KITT runtime operations "
                    "(repo.*, artifacts.*, patch.*, process.*, children.*, goal.*, memory.*, state.*, handles.*)."
                ),
            },
            {"name": "list_files", "description": "List files in directory"},
            {"name": "search", "description": "Search regex pattern across repository"},
            {"name": "read_file", "description": "Read file lines with start_line and end_line bounds"},
            {"name": "repository_map", "description": "Get compact indexed repository map"},
            {
                "name": "python_compute",
                "description": (
                    "Run a side-effect-free subset of Python for calculations and JSON data transformation. "
                    "Accepts code, optional JSON inputs, and result_var. No imports, files, network, shell, "
                    "reflection, functions, classes, threads, or external packages."
                ),
            },
            {"name": "write_file", "description": "Create or overwrite content to a file"},
            {"name": "apply_patch", "description": "Apply SEARCH/REPLACE diff blocks"},
            {"name": "run_command", "description": "Run shell command within security policy"},
            {"name": "git_status", "description": "Show uncommitted git status"},
            {"name": "git_diff", "description": "Show git diff"},
            {"name": "artifact_store", "description": "Persist bounded large output outside model context"},
            {"name": "artifact_read", "description": "Read a persisted artifact by id"},
            {"name": "artifact_list", "description": "List artifacts for this conversation"},
            {"name": "queue_input", "description": "Queue steering or follow-up input"},
            {"name": "goal_create", "description": "Create a bounded autonomous goal"},
            {"name": "goal_add_gate", "description": "Add a quality gate to an active goal"},
            {"name": "child_spawn", "description": "Spawn an isolated child task with restricted scope and budget"},
            {"name": "harness_remember", "description": "Persist a learned guideline entry"},
        ]
        schemas = {
            "kitt_runtime": {
                "operation": "string",
                "arguments": "JSON object with operation-specific parameters",
            },
            "list_files": {"path": "relative dir, default ."},
            "search": {"pattern": "literal text or regex", "regex": "bool, default false"},
            "read_file": {
                "path": "relative file, optional with around_symbol",
                "around_symbol": "indexed symbol name",
                "context_lines": "int, default 20",
                "start_line": "int >=1",
                "end_line": "int, max 5000 lines",
                "max_bytes": "int, optional output cap",
            },
            "repository_map": {
                "mode": "workspace|module|symbol|impact, default workspace",
                "query": "symbol/module/text",
                "path": "optional relative file/module path",
                "limit": "int <=500",
                "max_tokens": "int <=4000",
            },
            "python_compute": {
                "code": "safe Python subset",
                "inputs": "JSON object",
                "result_var": "name, default _result",
            },
            "write_file": {
                "path": "relative file",
                "content": "full file text",
                "expected_content_hash": "optional sha256",
            },
            "apply_patch": {"patch": "SEARCH/REPLACE blocks"},
            "run_command": {"command": "shell command allowed by policy"},
            "artifact_read": {"artifact_id": "id", "offset": "int", "limit": "int"},
            "goal_create": {"objective": "text", "token_budget": "optional int"},
            "goal_add_gate": {"command": "validation command"},
            "child_spawn": {"task": "text", "scope": "optional path/tool constraints"},
            "harness_remember": {"text": "guideline text"},
        }
        for tool in all_tools:
            schema = schemas.get(tool["name"])
            if schema is not None:
                tool["args"] = schema
        for custom_tool in self._custom_tools.values():
            all_tools.append(
                {
                    "name": custom_tool["name"],
                    "description": custom_tool["description"],
                    "args": custom_tool["args"],
                }
            )
        if enabled_tools is None:
            return all_tools
        enabled = set(enabled_tools)
        return [tool for tool in all_tools if tool["name"] in enabled]

    def _record_approved_principal_continuation(
        self,
        security_context,
        turn_id: str,
        result: ToolResult,
    ) -> None:
        """Record post-approval continuation without widening privileges."""
        principal_type = str(getattr(security_context, "principal_type", "") or "").upper()
        principal_id = str(getattr(security_context, "principal_id", "") or "").strip()
        if principal_type == "CHILD":
            if self.child_manager and hasattr(
                self.child_manager, "on_approved_action_executed"
            ):
                self.child_manager.on_approved_action_executed(
                    principal_id, turn_id, result.output
                )
            return

        if principal_type == "GOAL" and self.db is not None:
            from kitt.runtime.state import RuntimeStateStore

            store = RuntimeStateStore(
                self.db,
                security_context.workspace_id,
                security_context.conversation_id,
            )
            store.set(
                f"goal.resume:{principal_id}",
                {
                    "turn_id": turn_id,
                    "tool_output": str(result.output or "")[:32768],
                    "recorded_at": time.time(),
                },
                ttl_seconds=3600.0,
            )
            if self.goal_service is not None:
                self.goal_service.resume_after_approval(
                    principal_id,
                    conversation_id=security_context.conversation_id,
                )
            if self.event_bus is not None:
                self.event_bus.publish(
                    "GoalApprovalContinuationRecorded",
                    {"goal_id": principal_id, "turn_id": turn_id},
                )

    def _validate_goal_fence(
        self,
        security_context,
        *,
        allow_waiting_approval: bool = False,
    ) -> None:
        if security_context is None:
            return
        token = getattr(security_context, "fencing_token", None)
        if not token:
            return
        if self.db is None:
            raise PermissionError("Goal execution requires database-backed lease fencing")
        owner = getattr(security_context, "fencing_owner_id", None)
        raw_subject_type = getattr(security_context, "fencing_subject_type", None)
        subject_type = (
            str(raw_subject_type).upper() if raw_subject_type is not None else None
        )
        subject_id = getattr(security_context, "fencing_subject_id", None)
        subject_conversation_id = getattr(
            security_context, "fencing_subject_conversation_id", None
        )
        if subject_type not in {None, "GOAL"}:
            raise PermissionError("Goal fence subject type is invalid")
        if not subject_id and getattr(security_context, "principal_type", None) == "GOAL":
            subject_id = getattr(security_context, "principal_id", None)
        if (
            not subject_conversation_id
            and subject_id
            and (
                subject_type == "GOAL"
                or getattr(security_context, "principal_type", None) == "GOAL"
            )
        ):
            subject_conversation_id = getattr(security_context, "conversation_id", None)
        if not owner or not subject_id:
            raise PermissionError("Goal execution is missing scheduler lease fencing token")
        with self.db.get_connection() as connection:
            row = connection.execute(
                """SELECT state,lease_id,lease_owner_id,lease_expires_at
                   FROM goals WHERE id=? AND conversation_id=?""",
                (subject_id, subject_conversation_id or security_context.conversation_id),
            ).fetchone()
        if not row:
            raise PermissionError("Goal principal no longer exists in this conversation")
        if allow_waiting_approval and row["state"] == "WAITING_APPROVAL":
            return
        expires = float(row["lease_expires_at"] or 0.0)
        if (
            row["state"] != "RUNNING"
            or row["lease_id"] != token
            or row["lease_owner_id"] != owner
            or expires <= time.time()
        ):
            raise PermissionError("Goal scheduler lease lost; tool execution fenced")

    def execute_tool(
        self,
        tool_name: str,
        args: dict = None,
        turn_id: str = "default_turn",
        conversation_id: str = "default_conv",
        workspace_id: str = "default_ws",
        enabled_tools: Optional[list] = None,
        grant: Optional[ApprovalGrant] = None,
        expected_approval_id: Optional[str] = None,
        origin: str = "MODEL",
        security_context=None,
    ) -> ToolResult:
        args = args or {}

        if security_context is not None:
            try:
                security_context.assert_scope(workspace_id, conversation_id)
            except PermissionError as exc:
                return ToolResult(False, "", str(exc))

            from kitt.security.capabilities import CAP_MCP_CALL, TOOL_TO_CAPABILITY

            required_capability = TOOL_TO_CAPABILITY.get(tool_name)
            custom = self._custom_tools.get(tool_name)
            if custom and str(custom.get("owner") or "").startswith("mcp:"):
                required_capability = CAP_MCP_CALL
            if required_capability and not security_context.has_capability(required_capability):
                return ToolResult(
                    False,
                    "",
                    f"Capability '{required_capability}' required for tool '{tool_name}' (fail-closed).",
                )
            if (
                custom
                and security_context.is_path_scoped
                and not bool(custom.get("scope_aware"))
            ):
                return ToolResult(
                    False,
                    "",
                    f"Custom tool '{tool_name}' is not declared path-scope aware.",
                )

        if enabled_tools is not None and tool_name not in enabled_tools:
            return ToolResult(
                False,
                "",
                f"Tool '{tool_name}' is not enabled in ContextPlan.",
            )

        permission = self.policy.evaluate_tool(tool_name, args, origin=origin)
        approval_validated = False
        if permission == "DENY":
            return ToolResult(
                False,
                "",
                f"Execution denied by PolicyEngine for tool '{tool_name}'.",
            )

        if permission == "ASK":
            expected_hash = self.policy.generate_action_hash(tool_name, args)
            valid = self.approval_manager.validate_and_consume(
                grant,
                expected_hash,
                turn_id,
                conversation_id,
                workspace_id,
                expected_approval_id=expected_approval_id,
            )
            if not valid:
                return ToolResult(
                    False,
                    "",
                    f"Tool '{tool_name}' requires explicit user confirmation (ASK policy).",
                    requires_approval=True,
                )
            approval_validated = True

        try:
            self._validate_goal_fence(
                security_context,
                allow_waiting_approval=approval_validated,
            )
        except PermissionError as exc:
            return ToolResult(False, "", str(exc))

        handler_args = dict(args)
        if (
            tool_name == "apply_patch"
            and security_context is not None
            and getattr(security_context, "approval_integrity", None) is not None
        ):
            # The integrity manifest is deliberately injected only after the
            # approval hash has been validated, so it cannot change the action
            # the user approved.
            from kitt.tools.handlers.system import PATCH_INTEGRITY_KEY

            handler_args[PATCH_INTEGRITY_KEY] = dict(
                security_context.approval_integrity
            )

        context = ToolContext(
            registry=self,
            turn_id=turn_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            origin=origin,
            security_context=security_context,
            approval_grant=grant,
            expected_approval_id=expected_approval_id,
        )
        handler = self._handlers.get(tool_name)
        if not handler:
            return ToolResult(False, "", f"Tool '{tool_name}' execution not implemented.")
        try:
            result = handler.execute(handler_args, context)
            if result.success and grant is not None and security_context is not None:
                self._record_approved_principal_continuation(
                    security_context, turn_id, result
                )
            return result
        except Exception as exc:
            return ToolResult(False, "", f"Tool error: {exc}")

    @staticmethod
    def _format_repository_map(mode: str, rows: List[Dict[str, Any]]) -> str:
        if mode == "workspace":
            return "\n".join(
                f"{row['root_path']} | {row['kind']} | manifest={row['manifest_path'] or '-'} | files={row['files']}"
                for row in rows
            )
        if mode == "module":
            return "\n".join(
                f"{row['path']} | symbols={row['symbols']}" for row in rows
            )
        if mode == "symbol":
            return "\n".join(
                f"{row['path']}:{row['start_line']}-{row['end_line']} | {row['kind']} | {row['signature'] or row['name']}"
                for row in rows
            )
        if mode == "impact":
            return "\n".join(
                f"{row['source']} -> {row['target']} | {row['kind']} | weight={row['weight']}"
                for row in rows
            )
        return "\n".join(str(row) for row in rows)
