import re
import shlex
import hashlib
import time
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, List, Dict, Any, Optional
from kitt.domain.entities import Permission
from kitt.tools.path_policy import WorkspacePathPolicy

@dataclass(frozen=True)
class CommandRequest:
    executable: str
    argv: List[str]
    cwd: Path
    purpose: str = ""

class PolicyEngine:
    """Security policy engine for tool execution, argv parsing, and permission evaluation."""

    SHELL_OPERATORS_RE = re.compile(r'[;&|`$\n]')

    SAFE_READONLY_COMMANDS = {'git', 'rg', 'grep', 'ls', 'pwd'}
    DISALLOWED_SHELL_COMMANDS = {'cat', 'find', 'sudo', 'chmod', 'chown', 'dd', 'mkfs', 'curl', 'wget', 'nc', 'netcat', 'rm'}

    DENIED_GIT_FLAGS = {'--no-index', '-C', '--git-dir', '--work-tree'}

    def __init__(self, root_dir: str = ".", autonomy: Optional[Any] = None, approval_manager: Optional[Any] = None):
        from kitt.core.autonomy_policy import AutonomyPolicy
        self.root_path = Path(root_dir).resolve()
        self.path_policy = WorkspacePathPolicy(root_dir=root_dir)
        self.autonomy = autonomy or AutonomyPolicy.preset("supervised")
        self.approval_manager = approval_manager

    def _evaluate_tool_base(self, tool_name: str, args: dict = None, origin: str = 'MODEL') -> Permission:
        args = args or {}

        if origin == 'MODEL':
            if tool_name in {
                'list_files', 'search', 'read_file', 'repository_map', 'git_status',
                'git_diff', 'python_compute', 'artifact_list', 'artifact_read'
            }:
                return 'ALLOW'

            if tool_name == 'run_command':
                command = str(args.get("command", "")).strip()
                return self.evaluate_command(command)

            return 'ASK'

        if tool_name in {
            'list_files', 'search', 'read_file', 'repository_map', 'git_status',
            'git_diff', 'python_compute', 'artifact_list', 'artifact_read', 'artifact_store',
            'goal_create', 'goal_add_gate', 'queue_input', 'child_spawn', 'harness_remember'
        }:
            return 'ALLOW'

        if tool_name in {'apply_patch', 'write_file'}:
            return 'ASK'

        if tool_name == 'run_command':
            command = str(args.get("command", "")).strip()
            return self.evaluate_command(command)

        return 'ASK'

    def evaluate_tool(self, tool_name: str, args: dict = None, origin: str = 'MODEL') -> Permission:
        args = args or {}

        if getattr(self.autonomy, "level", "supervised") == "read_only":
            if tool_name in {'apply_patch', 'write_file', 'run_command', 'child_spawn', 'child'}:
                return 'DENY'

        # Check remembered approval rules in ApprovalManager first
        if self.approval_manager and tool_name in {'apply_patch', 'write_file'}:
            path = args.get("path") or args.get("file")
            rem = self.approval_manager.check_remembered(tool_name, path)
            if rem in ('allow', 'deny'):
                return 'ALLOW' if rem == 'allow' else 'DENY'

        base = self._evaluate_tool_base(tool_name, args, origin)
        if base != 'ASK':
            return base

        return self._autonomy_downgrade(tool_name, args, base)

    def _autonomy_downgrade(self, tool_name: str, args: dict, base: Permission) -> Permission:
        """Único ponto de decisão ASK->ALLOW por autonomia — usado por MODEL e não-MODEL."""
        if tool_name in {'apply_patch', 'write_file'} and getattr(self.autonomy, "allow_file_write_auto", False):
            return 'ALLOW'

        if tool_name == 'run_command':
            command = str(args.get("command", "")).strip()
            cmd_eval = self.evaluate_command(command)
            if cmd_eval == 'DENY':
                return 'DENY'
            if getattr(self.autonomy, "allow_run_command_auto", False):
                return 'ALLOW'
            return cmd_eval

        if tool_name in {'child_spawn', 'child'} and getattr(self.autonomy, "allow_child_spawn_auto", True):
            return 'ALLOW'

        return base

    def evaluate_command_request(self, req: CommandRequest) -> Permission:
        if not req.argv:
            return 'DENY'
        cmd_str = " ".join(shlex.quote(arg) for arg in req.argv)
        return self.evaluate_command(cmd_str)

    def evaluate_command(self, command: str) -> Permission:
        if not command:
            return 'DENY'

        if self.SHELL_OPERATORS_RE.search(command):
            return 'DENY'

        try:
            argv = shlex.split(command)
        except Exception:
            return 'DENY'

        if not argv:
            return 'DENY'

        executable = Path(argv[0]).name.lower()

        # Deny dangerous shell tools that bypass path policy (cat, find, etc)
        if executable in self.DISALLOWED_SHELL_COMMANDS:
            return 'DENY'

        # Check path arguments for containment in workspace root
        for arg in argv[1:]:
            if not arg.startswith("-"):
                # Potential path argument
                if arg.startswith("/") or arg.startswith("..") or arg.startswith("~"):
                    is_safe, _, _ = self.path_policy.validate_path(arg)
                    if not is_safe:
                        return 'DENY'

        # Check git escape flags
        if executable == 'git':
            for arg in argv[1:]:
                if arg in self.DENIED_GIT_FLAGS or any(arg.startswith(f"{f}=") for f in self.DENIED_GIT_FLAGS):
                    return 'DENY'

            subcmd = argv[1].lower() if len(argv) > 1 else ""
            if subcmd in {'status', 'diff', 'log', 'branch'}:
                return 'ALLOW'
            if subcmd in {'push', 'reset'}:
                return 'DENY'
            return 'ASK'

        # Safe read-only commands
        if executable in {'rg', 'grep', 'pwd'} and len(argv) == 1:
            return 'ALLOW'

        if executable == 'ls' and len(argv) <= 2:
            return 'ALLOW'

        # Build tools, python modules, pytest, npm scripts require explicit user approval (ASK)
        if executable in {'python', 'python3', 'pytest', 'npm', 'bun', 'mvn', 'gradle', 'cargo', 'go'}:
            return 'ASK'

        return 'ASK'

    @staticmethod
    def generate_action_hash(tool_name: str, args: Dict[str, Any]) -> str:
        serialized = f"{tool_name}:{json_serialize(args)}"
        return hashlib.sha256(serialized.encode('utf-8')).hexdigest()

def json_serialize(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True)
