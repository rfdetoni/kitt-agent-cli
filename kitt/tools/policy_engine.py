import re
import shlex
from typing import Literal, List
from kitt.domain.entities import Permission

class PolicyEngine:
    """Security policy engine for tool execution and shell command permissions."""

    SHELL_OPERATORS_RE = re.compile(r'[;&|`$\n]')

    ALLOW_COMMAND_PATTERNS = [
        re.compile(r'^\s*git\s+(status|diff|log|branch)\b'),
        re.compile(r'^\s*(rg|grep|find|cat|ls|pwd)\b'),
        re.compile(r'^\s*(python3?|pytest|npm|bun|mvn|gradle|cargo|go)\s+(test|check|build|run|-m)\b')
    ]

    DENY_COMMAND_PATTERNS = [
        re.compile(r'^\s*git\s+(push|reset\s+--hard)\b'),
        re.compile(r'^\s*(rm\s+-rf|sudo|chmod|chown|dd|mkfs)\b'),
        re.compile(r'^\s*(curl|wget|nc|netcat)\b')
    ]

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

        # Reject any shell command chaining or interpolation
        if self.SHELL_OPERATORS_RE.search(command):
            return 'DENY'

        for p in self.DENY_COMMAND_PATTERNS:
            if p.search(command):
                return 'DENY'

        for p in self.ALLOW_COMMAND_PATTERNS:
            if p.search(command):
                return 'ALLOW'

        return 'ASK'
