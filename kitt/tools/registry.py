import shlex
import re
import hashlib
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
        self.harness_service = None

    @property
    def repository_index(self):
        return getattr(self.context_engine, "index", None)

    def _refresh_index(self, paths: Optional[List[str]] = None) -> None:
        index = self.repository_index
        if index is not None:
            if paths and hasattr(index, "update_paths"):
                index.update_paths(paths)
            else:
                index.build_or_update()

    def attach_services(self, artifacts=None, queue_service=None, goal_service=None, child_manager=None, harness_service=None):
        self.artifacts = artifacts
        self.artifact_tools = ArtifactTools(artifacts) if artifacts else None
        self.queue_service = queue_service
        self.goal_service = goal_service
        self.goal_tools = GoalTools(goal_service) if goal_service else None
        self.child_manager = child_manager
        self.child_tools = ChildTools(child_manager) if child_manager else None
        self.harness_service = harness_service

    def get_tool_definitions(self, enabled_tools: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        all_tools = [
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
        if enabled_tools is None:
            return all_tools
        return [t for t in all_tools if t["name"] in enabled_tools]

    def execute_tool(self, tool_name: str, args: dict = None, turn_id: str = "default_turn",
                     conversation_id: str = "default_conv", workspace_id: str = "default_ws",
                     enabled_tools: Optional[list] = None, grant: Optional[ApprovalGrant] = None,
                     expected_approval_id: Optional[str] = None, origin: str = 'MODEL') -> ToolResult:
        args = args or {}

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

        try:
            if tool_name == "list_files":
                rel = args.get("path", ".")
                is_safe, target, err = self.path_policy.validate_path(rel)
                if not is_safe or not target or not target.exists():
                    return ToolResult(success=False, output="", error=err or "Access outside workspace denied.")
                files = [str(p.relative_to(self.root_path)) for p in target.glob("*") if p.is_file()][:100]
                return ToolResult(success=True, output="\n".join(files))

            elif tool_name == "read_file":
                rel = args.get("path", "")
                around_symbol = str(args.get("around_symbol", "") or "")
                if around_symbol and self.repository_index is not None:
                    symbol_row = self.repository_index.find_symbol_location(around_symbol, rel or None)
                    if not symbol_row:
                        return ToolResult(success=False, output="", error=f"Symbol not found: {around_symbol}")
                    rel = symbol_row["path"]
                    context_lines = max(0, min(int(args.get("context_lines", 20)), 200))
                    args["start_line"] = max(1, int(symbol_row["start_line"]) - context_lines)
                    args["end_line"] = int(symbol_row["end_line"]) + context_lines
                is_safe, target, err = self.path_policy.validate_path(rel)
                if not is_safe or not target or not target.exists() or not target.is_file():
                    return ToolResult(success=False, output="", error=err or "File not found or outside workspace.")
                start = max(1, int(args.get("start_line", 1))) - 1
                requested_end = int(args.get("end_line", start + 200))
                max_lines = 5000
                max_bytes = max(0, int(args.get("max_bytes", 0) or 0))
                end = min(requested_end, start + max_lines)
                out = []
                used_bytes = 0
                truncated = False
                with target.open("r", encoding="utf-8", errors="ignore") as fh:
                    for idx, line in enumerate(fh, 1):
                        if idx <= start:
                            continue
                        if idx > end:
                            break
                        line_text = line.rstrip("\n")
                        line_bytes = len((("\n" if out else "") + line_text).encode("utf-8"))
                        if max_bytes and used_bytes + line_bytes > max_bytes:
                            remaining = max_bytes - used_bytes - (1 if out else 0)
                            if remaining > 0:
                                out.append(line_text.encode("utf-8")[:remaining].decode("utf-8", errors="ignore"))
                            truncated = True
                            break
                        out.append(line_text)
                        used_bytes += line_bytes
                chunk = "\n".join(out)
                stat = target.stat()
                digest = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
                return ToolResult(
                    success=True,
                    output=chunk,
                    truncated=truncated,
                    metadata={
                        "content_hash": digest,
                        "hash_scope": "returned_range",
                        "path": rel,
                        "start_line": start + 1,
                        "end_line": start + len(out),
                        "file_size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                    },
                )

            elif tool_name == "search":
                pattern = str(args.get("pattern", ""))
                if not pattern or len(pattern) > 500:
                    return ToolResult(False, "", "Invalid search pattern.")
                if not bool(args.get("regex", False)) and self.repository_index is not None:
                    self._refresh_index()
                    rows = self.repository_index.search_text(pattern, limit=200)
                    terms = self.repository_index._query_terms(pattern)
                    matches = []
                    for row in rows:
                        path = row.get("path")
                        content = row.get("content", "")
                        if not path:
                            continue
                        for no, line in enumerate(content.splitlines(), 1):
                            if any(term.lower() in line.lower() for term in terms):
                                matches.append(f"{path}:{no}:{line[:300]}")
                                break
                        if len(matches) >= 200:
                            break
                    return ToolResult(True, "\n".join(matches), truncated=len(matches) >= 200, metadata={"method": "index"})
                try:
                    rx = re.compile(pattern)
                except re.error as exc:
                    return ToolResult(False, "", f"Invalid regex: {exc}")
                matches = []
                for path in RepositoryScanner(self.root_path).scan_files():
                    if len(matches) >= 200:
                        break
                    try:
                        rel_path = path.relative_to(self.root_path)
                        with path.open("r", encoding="utf-8", errors="ignore") as fh:
                            for no, line in enumerate(fh, 1):
                                if no > 5000:
                                    break
                                if rx.search(line):
                                    matches.append(f"{rel_path}:{no}:{line.rstrip()[:300]}")
                                    if len(matches) >= 200:
                                        break
                    except OSError:
                        continue
                return ToolResult(True, "\n".join(matches), truncated=len(matches) >= 200)

            elif tool_name == "repository_map":
                if self.repository_index is None:
                    return ToolResult(False, "", "Repository index unavailable.")
                self._refresh_index()
                mode = str(args.get("mode", "workspace") or "workspace")
                rows = self.repository_index.repository_map(
                    mode=mode,
                    query=str(args.get("query", "") or ""),
                    path=str(args.get("path", "") or ""),
                    limit=min(int(args.get("limit", 80)), 500),
                )
                output = self._format_repository_map(mode, rows)
                max_tokens = min(int(args.get("max_tokens", 1200)), 4000)
                max_chars = max_tokens * 4
                return ToolResult(
                    True,
                    output[:max_chars],
                    truncated=len(output) > max_chars,
                    metadata={"method": "index", "mode": mode, "rows": len(rows)},
                )

            elif tool_name == "write_file":
                rel = args.get("path", "") or args.get("file", "")
                content = args.get("content", "")
                is_safe, target, err = self.path_policy.validate_path(rel)
                if not is_safe or not target:
                    return ToolResult(success=False, output="", error=err or "Access outside workspace denied.")
                target.parent.mkdir(parents=True, exist_ok=True)
                expected_hash = args.get("expected_content_hash")
                if expected_hash and target.exists():
                    actual_hash = hashlib.sha256(target.read_bytes()).hexdigest()
                    if actual_hash != expected_hash:
                        return ToolResult(success=False, output="", error="expected_content_hash mismatch.")
                target.write_text(content, encoding="utf-8")
                self._refresh_index([str(target.relative_to(self.root_path))])
                new_hash = hashlib.sha256(target.read_bytes()).hexdigest()
                return ToolResult(success=True, output=f"Successfully wrote {len(content)} bytes to {rel}.", metadata={"content_hash": new_hash})

            elif tool_name == "apply_patch":
                patch_text = args.get("patch", "")
                blocks = self.parser.parse(patch_text)
                edit_res = self.applier.apply(blocks, root_dir=str(self.root_path), allow_overwrite_existing=True)
                if edit_res.success:
                    self._refresh_index(edit_res.applied_files + edit_res.created_files)
                    output = f"Applied edit to {len(edit_res.applied_files + edit_res.created_files)} file(s)."
                    return ToolResult(success=True, output=output, metadata={"edit_result": edit_res})
                else:
                    return ToolResult(success=False, output="", error="\n".join(edit_res.errors), metadata={"edit_result": edit_res})

            elif tool_name == "python_compute":
                execution = self.safe_python.execute(
                    code=args.get("code", ""),
                    inputs=args.get("inputs", {}),
                    result_var=args.get("result_var", "_result"),
                )
                return ToolResult(
                    success=execution.success,
                    output=execution.output,
                    error=execution.error,
                    bytes_count=len(execution.output.encode("utf-8")),
                    truncated=execution.truncated,
                )

            elif tool_name == "run_command":
                cmd_str = str(args.get("command", "")).strip()
                if not cmd_str:
                    return ToolResult(success=False, output="", error="Empty command.")

                try:
                    argv = shlex.split(cmd_str)
                except Exception as se:
                    return ToolResult(success=False, output="", error=f"Invalid shell command syntax: {se}")

                res = self.process_runner.run(argv, timeout_seconds=30)
                output = res.stdout + (("\n" + res.stderr) if res.stderr else "")
                return ToolResult(success=(res.returncode == 0 and not res.timed_out),
                    output=output, error=None if res.returncode == 0 else res.stderr,
                    bytes_count=len(output.encode()), truncated=res.truncated)

            elif tool_name in {"git_status", "git_diff"}:
                sub = ["status", "--short"] if tool_name == "git_status" else ["diff"]
                res = self.process_runner.run(["git"] + sub, timeout_seconds=30)
                return ToolResult(success=res.returncode == 0, output=res.stdout, error=res.stderr or None)

            elif tool_name == "artifact_store" and self.artifact_tools:
                artifact = self.artifact_tools.put(workspace_id, args.get("content", ""),
                    args.get("artifact_type", "TEXT"), args.get("summary", "Agent artifact"),
                    conversation_id, turn_id)
                return ToolResult(True, artifact.id, metadata={"artifact": artifact})
            elif tool_name == "artifact_read" and self.artifact_tools:
                raw = self.artifact_tools.read_text(str(args.get("artifact_id", "")))
                return ToolResult(True, raw, bytes_count=len(raw.encode()))
            elif tool_name == "artifact_list" and self.artifact_tools:
                items = self.artifact_tools.list(conversation_id, int(args.get("limit", 20)))
                return ToolResult(True, "\n".join(f"{a.id} {a.artifact_type} {a.size_bytes}B {a.summary}" for a in items))
            elif tool_name == "queue_input" and self.queue_service:
                kind = str(args.get("kind", "FOLLOW_UP")).upper()
                if kind not in {"STEERING", "FOLLOW_UP"}:
                    return ToolResult(False, "", "Invalid queue kind: must be STEERING or FOLLOW_UP.")
                item = (self.queue_service.steer if kind == "STEERING" else self.queue_service.follow_up)(
                    conversation_id, str(args.get("content", "")))
                return ToolResult(True, item.id)
            elif tool_name == "goal_create" and (self.goal_tools or self.goal_service):
                service = self.goal_tools or self.goal_service
                goal = service.create(conversation_id, str(args.get("objective", "")),
                    args.get("success_criteria", []), args.get("token_budget"),
                    int(args.get("max_turns", 12)), int(args.get("max_wall_seconds", 1800)))
                return ToolResult(True, goal.id if hasattr(goal, "id") else str(goal))
            elif tool_name == "goal_add_gate" and self.goal_service:
                gate = self.goal_service.add_gate(
                    goal_id=str(args.get("goal_id", "")),
                    name=str(args.get("name", "QualityGate")),
                    argv=args.get("argv", []),
                    timeout_seconds=int(args.get("timeout_seconds", 120))
                )
                return ToolResult(True, f"Gate '{gate.name}' added with ID {gate.id}.")
            elif tool_name == "child_spawn" and (self.child_tools or self.child_manager):
                mgr = self.child_tools or self.child_manager
                child = mgr.spawn(
                    parent_conversation_id=conversation_id,
                    parent_turn_id=turn_id,
                    name=str(args.get("name", "child_task")),
                    task=str(args.get("task", "")),
                    workspace_id=workspace_id,
                    allowed_paths=args.get("allowed_paths", []),
                    enabled_tools=args.get("enabled_tools") or args.get("allowed_tools") or ["read_file", "search"],
                    token_budget=int(args.get("token_budget", 4000)),
                    timeout_seconds=float(args.get("timeout_seconds", 60.0))
                )
                return ToolResult(True, f"Child task spawned with ID {child.id}.")
            elif tool_name == "harness_remember" and self.harness_service:
                evidence_raw = args.get("evidence", {})
                if isinstance(evidence_raw, str):
                    try:
                        evidence_raw = json.loads(evidence_raw)
                    except Exception:
                        evidence_raw = {"note": evidence_raw}
                entry = self.harness_service.remember(
                    name=str(args.get("name", "")),
                    content=str(args.get("content", "")),
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    evidence=evidence_raw
                )
                return ToolResult(True, f"Harness entry '{entry.name}' saved with ID {entry.id}.")

            return ToolResult(success=False, output="", error=f"Tool '{tool_name}' execution not implemented.")

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
