import re
import shlex
import hashlib
import time
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

@dataclass
class ApprovalToken:
    action_hash: str
    created_at: float = field(default_factory=time.time)
    turn_id: str = ""
    used: bool = False

class PolicyEngine:
    """Security policy engine for tool execution, argv parsing, and permission evaluation."""

    SHELL_OPERATORS_RE = re.compile(r'[;&|`$\n]')

    SAFE_READONLY_COMMANDS = {'git', 'rg', 'grep', 'ls', 'pwd'}
    DISALLOWED_SHELL_COMMANDS = {'cat', 'find', 'sudo', 'chmod', 'chown', 'dd', 'mkfs', 'curl', 'wget', 'nc', 'netcat', 'rm'}

    DENIED_GIT_FLAGS = {'--no-index', '-C', '--git-dir', '--work-tree'}

    def __init__(self, root_dir: str = "."):
        self.root_path = Path(root_dir).resolve()
        self.path_policy = WorkspacePathPolicy(root_dir=root_dir)

    def evaluate_tool(self, tool_name: str, args: dict = None) -> Permission:
        args = args or {}

        if tool_name in {'list_files', 'search', 'read_file', 'repository_map', 'git_status', 'git_diff'}:
            return 'ALLOW'

        if tool_name in {'apply_patch', 'write_file'}:
            return 'ASK'

        if tool_name == 'run_command':
            command = str(args.get("command", "")).strip()
            return self.evaluate_command(command)

        return 'ASK'

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
    import json
    return json.dumps(obj, sort_keys=True)
