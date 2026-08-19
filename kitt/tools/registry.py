import shlex
import re
import hashlib
import os
import tempfile
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Set
from kitt.tools.policy_engine import PolicyEngine
from kitt.tools.path_policy import WorkspacePathPolicy
from kitt.tools.approval import ApprovalGrant, ApprovalManager
from kitt.edit_format.applier import DiffApplier
from kitt.edit_format.parser import SearchReplaceParser
from kitt.tools.safe_python import SafePythonExecutor
from kitt.tools.process_runner import ProcessRunner
from kitt.context_engine.engine import ContextEngine
from kitt.index.repository import RepositoryIndex
from kitt.index.scanner import RepositoryScanner

from kitt.tools.artifact_tools import ArtifactTools
from kitt.tools.child_tools import ChildTools
from kitt.tools.goal_tools import GoalTools
from kitt.tools.handlers import ToolContext, ToolHandler
from kitt.tools.handlers.files import ListFilesHandler, ReadFileHandler, WriteFileHandler
from kitt.tools.handlers.search import SearchHandler, RepositoryMapHandler
from kitt.tools.handlers.system import PythonComputeHandler, ApplyPatchHandler, RunCommandHandler, GitStatusHandler, GitDiffHandler
from kitt.tools.handlers.services import (
    ArtifactStoreHandler, ArtifactReadHandler, ArtifactListHandler,
    QueueInputHandler, GoalCreateHandler, GoalAddGateHandler,
    ChildSpawnHandler, HarnessRememberHandler
)
from kitt.tools.handlers.safe_runtime import SafeRuntimeHandler

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
    """Registry managing executable tools, schemas, and path-contained policy enforcement."""

    def __init__(self, root_dir: str = ".", context_engine: ContextEngine | None = None):
        self.root_path = Path(root_dir).resolve()
        self.policy = PolicyEngine(root_dir=root_dir)
        self.path_policy = WorkspacePathPolicy(root_dir=root_dir)
        self.applier = DiffApplier()
        self.parser = SearchReplaceParser()
        self.approval_manager = ApprovalManager()
        self.safe_python = SafePythonExecutor()
        self.process_runner = ProcessRunner(root_dir)
        self.context_engine = context_engine or ContextEngine(repository_index=RepositoryIndex(self.root_path))
        self.artifacts = None
        self.artifact_tools = None
        self.queue_service = None
        self.goal_service = None
        self.goal_tools = None
        self.child_manager = None
        self.child_tools = None
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
    ) -> None:
        """Registers a dynamic tool from a plugin or MCP server."""
        class _CustomHandler:
            def __init__(self, fn):
                self.fn = fn

            def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
                if hasattr(self.fn, "execute"):
                    res = self.fn.execute(args)
                else:
                    res = self.fn(args)
                if isinstance(res, ToolResult):
                    return res
                return ToolResult(success=True, output=str(res))

        wrapped_handler = handler if (hasattr(handler, "execute") and callable(getattr(handler, "execute")) and not callable(handler)) else _CustomHandler(handler)
        self._handlers[tool_name] = wrapped_handler
        self._custom_tools[tool_name] = {
            "name": tool_name,
            "description": description or f"Custom tool {tool_name}",
            "args": schema or {},
            "owner": owner_plugin_id,
        }
        if hasattr(self.policy, "allow_custom_tool"):
            self.policy.allow_custom_tool(tool_name)

    def unregister_by_owner(self, owner_plugin_id: str) -> int:
        """Unregisters all tools owned by a plugin or MCP server."""
        to_remove = [k for k, v in self._custom_tools.items() if v.get("owner") == owner_plugin_id]
        for k in to_remove:
            self._handlers.pop(k, None)
            self._custom_tools.pop(k, None)
            if hasattr(self.policy, "disallow_custom_tool"):
                self.policy.disallow_custom_tool(k)
        return len(to_remove)

    @property
    def repository_index(self):
        return getattr(self.context_engine, "index", None)

    def _refresh_index(self, paths: Optional[List[str]] = None) -> None:
        index = self.repository_index
        if index is not None:
            if paths and hasattr(index, "update_paths"):
                index.update_paths(paths)
            elif index.index_generation() == 0:
                index.build_or_update()

    def attach_services(self, artifacts=None, queue_service=None, goal_service=None, child_manager=None, harness_service=None, memory_service=None, skill_manager=None, db=None):
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

    def get_tool_definitions(self, enabled_tools: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        all_tools = [
            {
                "name": "kitt_runtime",
                "description": "Execute safe, compact, policy-governed KITT runtime operations (repo.*, artifacts.*, patch.*, process.*, children.*, goal.*, state.*, handles.*).",
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
                    "reflection, functions, classes, threads, or external packages. Assign the final value "
                    "to _result (or result_var)."
                )
            },
            {"name": "write_file", "description": "Create or overwrite content to a file at specified path (arguments: path, content)"},
            {"name": "apply_patch", "description": "Apply SEARCH/REPLACE diff blocks"},
            {"name": "run_command", "description": "Run shell command within security policy"},
            {"name": "git_status", "description": "Show uncommitted git status"},
            {"name": "git_diff", "description": "Show git diff"}
            ,{"name": "artifact_store", "description": "Persist bounded large output outside model context"}
            ,{"name": "artifact_read", "description": "Read a persisted artifact by id"}
            ,{"name": "artifact_list", "description": "List artifacts for this conversation"}
            ,{"name": "queue_input", "description": "Queue steering or follow-up input"}
            ,{"name": "goal_create", "description": "Create a bounded autonomous goal"}
            ,{"name": "goal_add_gate", "description": "Add a quality gate (command check) to an active goal"}
            ,{"name": "child_spawn", "description": "Spawn an isolated child task with restricted scope and budget"}
            ,{"name": "harness_remember", "description": "Persist a learned guideline entry into the harness repository"}
        ]
        arg_schemas = {
            "kitt_runtime": {
                "operation": "string (e.g. repo.read, repo.search, repo.inspect_symbol, patch.apply, process.run, children.spawn, children.send, children.inspect, goal.inspect, goal.update, state.get, state.set, handles.resolve)",
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
            "python_compute": {"code": "safe Python subset", "inputs": "JSON object", "result_var": "name, default _result"},
            "write_file": {"path": "relative file", "content": "full file text", "expected_content_hash": "optional sha256"},
            "apply_patch": {"patch": "SEARCH/REPLACE blocks"},
            "run_command": {"command": "shell command allowed by policy"},
            "artifact_read": {"artifact_id": "id", "offset": "int", "limit": "int"},
            "goal_create": {"objective": "text", "token_budget": "optional int"},
            "goal_add_gate": {"command": "validation command"},
            "child_spawn": {"task": "text", "scope": "optional path/tool constraints"},
            "harness_remember": {"text": "guideline text"},
        }
        for tool in all_tools:
            if tool["name"] in arg_schemas:
                tool["args"] = arg_schemas[tool["name"]]
        for custom_tool in self._custom_tools.values():
            all_tools.append({
                "name": custom_tool["name"],
                "description": custom_tool["description"],
                "args": custom_tool["args"],
            })
        if enabled_tools is None:
            return all_tools
        return [t for t in all_tools if t["name"] in enabled_tools]

    def execute_tool(self, tool_name: str, args: dict = None, turn_id: str = "default_turn",
                     conversation_id: str = "default_conv", workspace_id: str = "default_ws",
                     enabled_tools: Optional[list] = None, grant: Optional[ApprovalGrant] = None,
                     expected_approval_id: Optional[str] = None, origin: str = 'MODEL',
                     security_context=None) -> ToolResult:
        args = args or {}

        if security_context is not None:
            try:
                security_context.assert_scope(workspace_id, conversation_id)
            except PermissionError as exc:
                return ToolResult(success=False, output="", error=str(exc))
            from kitt.security.capabilities import TOOL_TO_CAPABILITY, CAP_MCP_CALL
            required_cap = TOOL_TO_CAPABILITY.get(tool_name)
            custom = self._custom_tools.get(tool_name)
            if custom and str(custom.get("owner") or "").startswith("mcp:"):
                required_cap = CAP_MCP_CALL
            if required_cap and not security_context.has_capability(required_cap):
                return ToolResult(
                    success=False, output="",
                    error=f"Capability '{required_cap}' required for tool '{tool_name}' (fail-closed)."
                )

        if enabled_tools is not None and tool_name not in enabled_tools:
            return ToolResult(
                success=False,
                output="",
                error=f"Tool '{tool_name}' is not enabled in ContextPlan."
            )

        perm = self.policy.evaluate_tool(tool_name, args, origin=origin)
        if perm == 'DENY':
            return ToolResult(
                success=False,
                output="",
                error=f"Execution denied by PolicyEngine for tool '{tool_name}'."
            )

        if perm == 'ASK':
            expected_hash = self.policy.generate_action_hash(tool_name, args)
            valid = self.approval_manager.validate_and_consume(
                grant, expected_hash, turn_id, conversation_id, workspace_id,
                expected_approval_id=expected_approval_id
            )
            if not valid:
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Tool '{tool_name}' requires explicit user confirmation (ASK policy).",
                    requires_approval=True
                )

        ctx = ToolContext(
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
            return ToolResult(success=False, output="", error=f"Tool '{tool_name}' execution not implemented.")
        try:
            return handler.execute(args, ctx)
        except Exception as e:
            return ToolResult(success=False, output="", error=f"Tool error: {e}")

    @staticmethod
    def _format_repository_map(mode: str, rows: List[Dict[str, Any]]) -> str:
        if mode == "workspace":
            return "\n".join(
                f"{row['root_path']} | {row['kind']} | manifest={row['manifest_path'] or '-'} | files={row['files']}"
                for row in rows
            )
        if mode == "module":
            return "\n".join(f"{row['path']} | symbols={row['symbols']}" for row in rows)
        if mode == "symbol":
            return "\n".join(
                f"{row['path']}:{row['start_line']}-{row['end_line']} | {row['kind']} | {row['signature'] or row['name']}"
                for row in rows
            )
        if mode == "impact":
            return "\n".join(f"{row['source']} -> {row['target']} | {row['kind']} | weight={row['weight']}" for row in rows)
        return "\n".join(str(row) for row in rows)
