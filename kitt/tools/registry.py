import shlex
import subprocess
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Set
from kitt.tools.policy_engine import PolicyEngine
from kitt.edit_format.applier import DiffApplier
from kitt.edit_format.parser import SearchReplaceParser

@dataclass
class ToolContext:
    root_dir: Path
    policy: PolicyEngine = field(default_factory=PolicyEngine)

@dataclass
class ToolResult:
    success: bool
    output: str
    error: Optional[str] = None
    bytes_count: int = 0
    truncated: bool = False
    requires_approval: bool = False

class ToolRegistry:
    """Registry managing executable tools, schemas, and policy enforcement."""

    def __init__(self, root_dir: str = "."):
        self.root_path = Path(root_dir).resolve()
        self.policy = PolicyEngine()
        self.applier = DiffApplier()
        self.parser = SearchReplaceParser()

    def get_tool_definitions(self, enabled_tools: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        all_tools = [
            {"name": "list_files", "description": "List files in directory"},
            {"name": "search", "description": "Search regex pattern across repository"},
            {"name": "read_file", "description": "Read file lines with start_line and end_line bounds"},
            {"name": "repository_map", "description": "Get repository AST symbol map"},
            {"name": "apply_patch", "description": "Apply SEARCH/REPLACE diff blocks"},
            {"name": "run_command", "description": "Run shell command within security policy"},
            {"name": "git_status", "description": "Show uncommitted git status"},
            {"name": "git_diff", "description": "Show git diff"}
        ]
        if enabled_tools is None:
            return all_tools
        return [t for t in all_tools if t["name"] in enabled_tools]

    def execute_tool(
        self,
        tool_name: str,
        args: Dict[str, Any],
        enabled_tools: Optional[List[str]] = None,
        approved: bool = False
    ) -> ToolResult:
        if enabled_tools is not None and tool_name not in enabled_tools:
            return ToolResult(
                success=False,
                output="",
                error=f"Tool '{tool_name}' is not enabled in ContextPlan."
            )

        perm = self.policy.evaluate_tool(tool_name, args)
        if perm == 'DENY':
            return ToolResult(
                success=False,
                output="",
                error=f"Execution denied by PolicyEngine for tool '{tool_name}'."
            )

        if perm == 'ASK' and not approved:
            return ToolResult(
                success=False,
                output="",
                error=f"Tool '{tool_name}' requires explicit user confirmation (ASK policy).",
                requires_approval=True
            )

        try:
            if tool_name == "list_files":
                rel = args.get("path", ".")
                target = (self.root_path / rel).resolve()
                if not target.is_relative_to(self.root_path):
                    return ToolResult(success=False, output="", error="Access outside workspace denied.")
                files = [str(p.relative_to(self.root_path)) for p in target.glob("*") if p.is_file()][:100]
                return ToolResult(success=True, output="\n".join(files))

            elif tool_name == "read_file":
                rel = args.get("path", "")
                target = (self.root_path / rel).resolve()
                if not target.is_relative_to(self.root_path) or not target.exists():
                    return ToolResult(success=False, output="", error="File not found or outside workspace.")
                lines = target.read_text(encoding='utf-8', errors='ignore').splitlines()
                start = max(1, int(args.get("start_line", 1))) - 1
                end = min(len(lines), int(args.get("end_line", start + 200)))
                chunk = "\n".join(lines[start:end])
                return ToolResult(success=True, output=chunk)

            elif tool_name == "apply_patch":
                patch_text = args.get("patch", "")
                blocks = self.parser.parse(patch_text)
                edit_res = self.applier.apply(blocks, root_dir=str(self.root_path))
                if edit_res.success:
                    output = f"Applied edit to {len(edit_res.applied_files + edit_res.created_files)} file(s)."
                    return ToolResult(success=True, output=output)
                else:
                    return ToolResult(success=False, output="", error="\n".join(edit_res.errors))

            elif tool_name == "run_command":
                cmd_str = str(args.get("command", "")).strip()
                if not cmd_str:
                    return ToolResult(success=False, output="", error="Empty command.")

                try:
                    argv = shlex.split(cmd_str)
                except Exception as se:
                    return ToolResult(success=False, output="", error=f"Invalid shell command syntax: {se}")

                res = subprocess.run(
                    argv,
                    shell=False,
                    cwd=self.root_path,
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                output = res.stdout if res.returncode == 0 else (res.stdout + "\n" + res.stderr)
                return ToolResult(success=(res.returncode == 0), output=output, error=None if res.returncode == 0 else res.stderr)

            elif tool_name in {"git_status", "git_diff"}:
                sub = ["status", "--short"] if tool_name == "git_status" else ["diff"]
                res = subprocess.run(["git"] + sub, cwd=self.root_path, capture_output=True, text=True)
                return ToolResult(success=True, output=res.stdout)

            return ToolResult(success=False, output="", error=f"Tool '{tool_name}' execution not implemented.")

        except Exception as e:
            return ToolResult(success=False, output="", error=f"Tool error: {e}")
