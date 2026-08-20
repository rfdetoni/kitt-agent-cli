from __future__ import annotations

import hashlib
import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Set

from kitt.domain.entities import Permission
from kitt.tools.path_policy import WorkspacePathPolicy


@dataclass(frozen=True)
class CommandRequest:
    executable: str
    argv: List[str]
    cwd: Path
    purpose: str = ""


class PolicyEngine:
    """Conservative policy engine; only demonstrably read-only commands auto-ALLOW."""

    SHELL_OPERATORS_RE = re.compile(r"[;&|`$\n]")
    DISALLOWED_SHELL_COMMANDS = {
        "cat", "find", "sudo", "chmod", "chown", "dd", "mkfs",
        "curl", "wget", "nc", "netcat", "rm",
    }
    DENIED_GIT_FLAGS = {
        "--no-index", "-C", "--git-dir", "--work-tree", "--exec-path",
        "--config-env",
    }
    _SAFE_GIT_STATUS_FLAGS = {
        "--short", "-s", "--porcelain", "--porcelain=v1", "--porcelain=v2",
        "--branch", "-b", "--show-stash", "--no-renames",
    }
    _SAFE_GIT_DIFF_FLAGS = {
        "--stat", "--numstat", "--shortstat", "--name-only", "--name-status",
        "--check", "--summary", "--patch", "-p", "-U0", "--no-ext-diff",
        "--no-textconv", "--no-renames",
    }
    _SAFE_GIT_LOG_FLAGS = {
        "--oneline", "--decorate", "--no-decorate", "--stat", "--shortstat",
        "--name-only", "--name-status", "--no-patch",
    }

    def __init__(
        self,
        root_dir: str = ".",
        autonomy: Optional[Any] = None,
        approval_manager: Optional[Any] = None,
    ):
        from kitt.core.autonomy_policy import AutonomyPolicy
        self.root_path = Path(root_dir).resolve()
        self.path_policy = WorkspacePathPolicy(root_dir=root_dir)
        self.autonomy = autonomy or AutonomyPolicy.preset("supervised")
        self.approval_manager = approval_manager
        self._allowed_custom_tools: Set[str] = set()

    def allow_custom_tool(self, tool_name: str) -> None:
        self._allowed_custom_tools.add(tool_name)

    def disallow_custom_tool(self, tool_name: str) -> None:
        self._allowed_custom_tools.discard(tool_name)

    def _evaluate_tool_base(
        self,
        tool_name: str,
        args: dict | None = None,
        origin: str = "MODEL",
    ) -> Permission:
        args = args or {}
        if tool_name in self._allowed_custom_tools:
            return "ALLOW" if origin == "SAFE_RUNTIME_BROKER" else "ASK"

        read_tools = {
            "list_files", "search", "read_file", "repository_map",
            "git_status", "git_diff", "python_compute", "artifact_list",
            "artifact_read",
        }
        if origin == "MODEL":
            if tool_name == "kitt_runtime" or tool_name in read_tools:
                return "ALLOW"
            if tool_name == "run_command":
                return self.evaluate_command(str(args.get("command", "")).strip())
            return "ASK"

        if tool_name in read_tools | {
            "artifact_store", "goal_create", "goal_add_gate", "queue_input",
            "child_spawn", "harness_remember",
        }:
            return "ALLOW"
        if tool_name in {"apply_patch", "write_file"}:
            return "ASK"
        if tool_name == "run_command":
            return self.evaluate_command(str(args.get("command", "")).strip())
        return "ASK"

    def evaluate_tool(
        self,
        tool_name: str,
        args: dict | None = None,
        origin: str = "MODEL",
    ) -> Permission:
        args = args or {}
        if getattr(self.autonomy, "level", "supervised") == "read_only":
            if tool_name in {"apply_patch", "write_file", "run_command", "child_spawn", "child"}:
                return "DENY"

        if self.approval_manager and tool_name in {"apply_patch", "write_file"}:
            path = args.get("path") or args.get("file")
            remembered = self.approval_manager.check_remembered(tool_name, path)
            if remembered in {"allow", "deny"}:
                return "ALLOW" if remembered == "allow" else "DENY"

        base = self._evaluate_tool_base(tool_name, args, origin)
        if base != "ASK":
            return base
        return self._autonomy_downgrade(tool_name, args, base)

    def _autonomy_downgrade(
        self,
        tool_name: str,
        args: dict,
        base: Permission,
    ) -> Permission:
        if tool_name in {"apply_patch", "write_file"} and getattr(
            self.autonomy, "allow_file_write_auto", False
        ):
            return "ALLOW"
        if tool_name == "run_command":
            command = str(args.get("command", "")).strip()
            command_decision = self.evaluate_command(command)
            if command_decision == "DENY":
                return "DENY"
            if getattr(self.autonomy, "allow_run_command_auto", False):
                return "ALLOW"
            return command_decision
        if tool_name in {"child_spawn", "child"} and getattr(
            self.autonomy, "allow_child_spawn_auto", True
        ):
            return "ALLOW"
        return base

    def evaluate_command_request(self, req: CommandRequest) -> Permission:
        if not req.argv:
            return "DENY"
        return self.evaluate_command(" ".join(shlex.quote(arg) for arg in req.argv))

    def _path_arg_safe(self, arg: str) -> bool:
        if arg.startswith(("/", "..", "~")):
            safe, _, _ = self.path_policy.validate_path(arg)
            return bool(safe)
        return True

    @classmethod
    def _git_readonly(cls, argv: list[str]) -> Permission:
        if len(argv) < 2:
            return "DENY"
        for arg in argv[1:]:
            if arg in cls.DENIED_GIT_FLAGS or any(
                arg.startswith(flag + "=") for flag in cls.DENIED_GIT_FLAGS
            ):
                return "DENY"
            if arg in {"--ext-diff", "--textconv", "--output"} or arg.startswith("--output="):
                return "DENY"

        subcmd = argv[1].lower()
        args = argv[2:]

        # Options with values are deliberately narrow. Unknown forms are ASK,
        # never ALLOW.
        if subcmd == "status":
            for arg in args:
                if arg == "--":
                    continue
                if arg.startswith("--untracked-files="):
                    if arg.split("=", 1)[1] not in {"no", "normal", "all"}:
                        return "ASK"
                    continue
                if arg.startswith("-") and arg not in cls._SAFE_GIT_STATUS_FLAGS:
                    return "ASK"
            return "ALLOW"

        if subcmd == "diff":
            pathspec = False
            for arg in args:
                if arg == "--":
                    pathspec = True
                    continue
                if pathspec:
                    continue
                if arg.startswith("-U") and arg[2:].isdigit():
                    continue
                if arg.startswith("-") and arg not in cls._SAFE_GIT_DIFF_FLAGS:
                    return "ASK"
            return "ALLOW"

        if subcmd == "log":
            for arg in args:
                if arg.startswith("-n") and arg[2:].isdigit():
                    continue
                if arg.startswith("--max-count=") and arg.split("=", 1)[1].isdigit():
                    continue
                if arg.startswith("-") and arg not in cls._SAFE_GIT_LOG_FLAGS:
                    return "ASK"
            return "ALLOW"

        if subcmd == "branch":
            if not args or args == ["--show-current"] or args == ["--list"]:
                return "ALLOW"
            return "ASK"

        if subcmd in {"push", "reset", "clean"}:
            return "DENY"
        return "ASK"

    def evaluate_command(self, command: str) -> Permission:
        if not command or self.SHELL_OPERATORS_RE.search(command):
            return "DENY"
        try:
            argv = shlex.split(command)
        except Exception:
            return "DENY"
        if not argv:
            return "DENY"

        executable = Path(argv[0]).name.lower()
        if executable in self.DISALLOWED_SHELL_COMMANDS:
            return "DENY"
        if any(not self._path_arg_safe(arg) for arg in argv[1:] if not arg.startswith("-")):
            return "DENY"

        if executable == "git":
            return self._git_readonly(argv)
        if executable in {"pwd"} and len(argv) == 1:
            return "ALLOW"
        # rg/grep may traverse symlink/path targets and accept execution-like
        # options. Dedicated repository search is the auto-allowed surface.
        if executable in {"rg", "grep", "ls"}:
            return "ASK"
        if executable in {
            "python", "python3", "pytest", "npm", "bun", "mvn",
            "gradle", "cargo", "go",
        }:
            return "ASK"
        return "ASK"

    @staticmethod
    def generate_action_hash(tool_name: str, args: Dict[str, Any]) -> str:
        serialized = f"{tool_name}:{json_serialize(args)}"
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def json_serialize(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True)
