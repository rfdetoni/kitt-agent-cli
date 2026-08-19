from __future__ import annotations

import ast
import concurrent.futures
import datetime
import json
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Set

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
    validate_capabilities,
)


@dataclass
class SkillResult:
    success: bool
    data: Any = None
    error: Optional[str] = None
    artifacts_created: List[str] = field(default_factory=list)
    duration_ms: float = 0.0


@dataclass
class ExecutableSkillMetadata:
    name: str
    description: str
    version: str = "1.0.0"
    author: str = "Unknown"
    capabilities: List[str] = field(default_factory=list)
    requires_approval: List[str] = field(default_factory=list)
    path: Path = Path(".")
    is_executable: bool = False


# Security: AST verification of untrusted skill code
FORBIDDEN_MODULES = {
    "os", "sys", "subprocess", "socket", "shutil", "importlib", "builtins",
    "posix", "nt", "ctypes", "threading", "multiprocessing", "signal",
    "inspect", "pickle", "urllib", "http", "requests", "aiohttp", "pathlib",
    "code", "codeop", "pty", "commands",
}

FORBIDDEN_CALLS = {
    "eval", "exec", "open", "compile", "__import__", "globals", "locals",
    "vars", "breakpoint", "input", "exit", "quit", "getattr", "setattr", "delattr",
}

FORBIDDEN_ATTRIBUTES = {
    "__subclasses__", "__bases__", "__globals__", "__code__", "__closure__",
    "__mro__", "__import__", "__builtins__", "__dict__",
}


def validate_skill_ast(source: str) -> None:
    """Statically validates that skill code does not use prohibited modules, calls, or reflection."""
    tree = ast.parse(source)

    for node in ast.walk(tree):
        # 1. Block prohibited imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = alias.name.split(".")[0]
                if mod in FORBIDDEN_MODULES:
                    raise PermissionError(f"Import of forbidden module '{mod}' blocked in executable skill")
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mod = node.module.split(".")[0]
                if mod in FORBIDDEN_MODULES:
                    raise PermissionError(f"Import from forbidden module '{mod}' blocked in executable skill")

        # 2. Block prohibited function calls
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALLS:
                raise PermissionError(f"Call to prohibited function '{node.func.id}' blocked in executable skill")

        # 3. Block dangerous attributes / reflection
        elif isinstance(node, ast.Attribute):
            if node.attr in FORBIDDEN_ATTRIBUTES:
                raise PermissionError(f"Access to dangerous attribute '{node.attr}' blocked in executable skill")


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    mod_root = name.split(".")[0]
    if mod_root in FORBIDDEN_MODULES:
        raise PermissionError(f"Import of forbidden module '{mod_root}' is blocked in executable skill")
    return __import__(name, globals, locals, fromlist, level)


SAFE_BUILTINS = {
    "__import__": _safe_import,
    "abs": abs, "all": all, "any": any, "ascii": ascii, "bin": bin, "bool": bool,
    "bytearray": bytearray, "bytes": bytes, "chr": chr, "dict": dict, "divmod": divmod,
    "enumerate": enumerate, "filter": filter, "float": float, "format": format,
    "frozenset": frozenset, "hasattr": hasattr, "hash": hash, "hex": hex, "int": int,
    "isinstance": isinstance, "issubclass": issubclass, "iter": iter, "len": len,
    "list": list, "map": map, "max": max, "min": min, "next": next, "oct": oct,
    "ord": ord, "pow": pow, "print": lambda *a, **k: None, "range": range, "repr": repr,
    "reversed": reversed, "round": round, "set": set, "slice": slice, "sorted": sorted,
    "str": str, "sum": sum, "tuple": tuple, "type": type, "zip": zip,
    "Exception": Exception, "ValueError": ValueError, "KeyError": KeyError,
    "TypeError": TypeError, "IndexError": IndexError, "RuntimeError": RuntimeError,
    "PermissionError": PermissionError, "True": True, "False": False, "None": None,
}


class SkillExecutionContext:
    """Restricted capability facade passed into executable skills."""

    def __init__(
        self,
        metadata: ExecutableSkillMetadata,
        runtime: Any,
        call_stack: Optional[Set[str]] = None,
        max_depth: int = 3,
    ):
        self.metadata = metadata
        self.runtime = runtime
        self.call_stack = call_stack or {metadata.name}
        self.max_depth = max_depth
        self.declared_caps = set(metadata.capabilities)

    def _require_capability(self, cap: str) -> None:
        if cap not in self.declared_caps:
            raise PermissionError(
                f"Skill '{self.metadata.name}' attempted '{cap}' without declaring capability in frontmatter"
            )
        if cap in self.metadata.requires_approval:
            approval = getattr(self.runtime, "approval_manager", None) or getattr(getattr(self.runtime, "registry", None), "approval_manager", None)
            if approval and hasattr(approval, "check_approval"):
                if not approval.check_approval(cap):
                    raise PermissionError(f"Skill '{self.metadata.name}' operation '{cap}' requires user approval")

    def search_repo(self, pattern: str, regex: bool = False) -> Any:
        self._require_capability(CAP_REPO_SEARCH)
        res = self.runtime.execute("repo.search", {"pattern": pattern, "regex": regex}, effective_capabilities=self.declared_caps)
        if not res.success:
            raise RuntimeError(f"repo.search failed: {res.error}")
        return res.data

    def read_file(self, path: str, start_line: int = 1, end_line: int = 100) -> str:
        self._require_capability(CAP_REPO_READ)
        res = self.runtime.execute("repo.read", {"path": path, "start_line": start_line, "end_line": end_line}, effective_capabilities=self.declared_caps)
        if not res.success:
            raise RuntimeError(f"repo.read failed: {res.error}")
        return str(res.data or "")

    def inspect_symbol(self, symbol: str) -> Any:
        self._require_capability(CAP_REPO_READ)
        res = self.runtime.execute("repo.inspect_symbol", {"symbol": symbol}, effective_capabilities=self.declared_caps)
        if not res.success:
            raise RuntimeError(f"repo.inspect_symbol failed: {res.error}")
        return res.data

    def store_artifact(self, content: str, artifact_type: str = "SKILL_OUTPUT", summary: str = "") -> str:
        self._require_capability(CAP_ARTIFACT_WRITE)
        res = self.runtime.execute(
            "artifacts.store",
            {"content": content, "artifact_type": artifact_type, "summary": summary},
            effective_capabilities=self.declared_caps,
        )
        if not res.success:
            raise RuntimeError(f"artifacts.store failed: {res.error}")
        return str(res.data or "")

    def read_artifact(self, artifact_id: str) -> str:
        self._require_capability(CAP_ARTIFACT_READ)
        res = self.runtime.execute("artifacts.read", {"artifact_id": artifact_id}, effective_capabilities=self.declared_caps)
        if not res.success:
            raise RuntimeError(f"artifacts.read failed: {res.error}")
        return str(res.data or "")

    def apply_patch(self, patch: str) -> Any:
        self._require_capability(CAP_REPO_WRITE)
        res = self.runtime.execute("patch.apply", {"patch": patch}, effective_capabilities=self.declared_caps)
        if not res.success:
            raise RuntimeError(f"patch.apply failed: {res.error}")
        return res.data

    def run_command(self, command: str) -> Any:
        self._require_capability(CAP_PROCESS_RUN)
        res = self.runtime.execute("process.run", {"command": command}, effective_capabilities=self.declared_caps)
        if not res.success:
            raise RuntimeError(f"process.run failed: {res.error}")
        return res.data

    def spawn_child(self, name: str, task: str, allowed_paths: Optional[List[str]] = None) -> Any:
        self._require_capability(CAP_CHILD_SPAWN)
        res = self.runtime.execute(
            "children.spawn",
            {"name": name, "task": task, "allowed_paths": allowed_paths or []},
            effective_capabilities=self.declared_caps,
        )
        if not res.success:
            raise RuntimeError(f"children.spawn failed: {res.error}")
        return res.data

    def call_skill(self, skill_name: str, arguments: Dict[str, Any]) -> SkillResult:
        if len(self.call_stack) >= self.max_depth:
            raise RuntimeError(f"Maximum skill call depth ({self.max_depth}) exceeded")
        if skill_name in self.call_stack:
            raise RuntimeError(f"Skill cycle detected: '{skill_name}' is already in execution stack {self.call_stack}")

        new_stack = set(self.call_stack)
        new_stack.add(skill_name)
        runner = ExecutableSkillRunner(self.runtime)
        return runner.execute(skill_name, arguments, call_stack=new_stack, parent_capabilities=self.declared_caps)


class ExecutableSkillRunner:
    """Discovers, loads, and securely runs executable skills."""

    def __init__(self, runtime: Any, timeout_seconds: float = 30.0):
        self.runtime = runtime
        self.timeout = timeout_seconds

    def parse_metadata(self, skill_dir: Path) -> Optional[ExecutableSkillMetadata]:
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            return None

        content = skill_md.read_text(encoding="utf-8", errors="ignore")
        match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
        meta_dict: Dict[str, Any] = {}
        capabilities: List[str] = []
        requires_approval: List[str] = []

        if match:
            lines = match.group(1).splitlines()
            current_list_key = None
            for line in lines:
                stripped = line.strip()
                if not stripped:
                    continue
                if stripped.startswith("- ") and current_list_key:
                    val = stripped[2:].strip().strip("\"'")
                    if current_list_key == "capabilities":
                        capabilities.append(val)
                    elif current_list_key == "requires_approval":
                        requires_approval.append(val)
                elif ":" in stripped:
                    k, v = stripped.split(":", 1)
                    k = k.strip().lower()
                    v = v.strip().strip("\"'")
                    current_list_key = k if not v else None
                    meta_dict[k] = v

        name = meta_dict.get("name", skill_dir.name)
        desc = meta_dict.get("description", "")
        version = meta_dict.get("version", "1.0.0")
        author = meta_dict.get("author", "Unknown")

        if capabilities:
            validate_capabilities(capabilities)

        is_executable = (skill_dir / "skill.py").exists()

        return ExecutableSkillMetadata(
            name=name,
            description=desc,
            version=version,
            author=author,
            capabilities=capabilities,
            requires_approval=requires_approval,
            path=skill_dir,
            is_executable=is_executable,
        )

    def execute(
        self,
        skill_name: str,
        arguments: Dict[str, Any],
        call_stack: Optional[Set[str]] = None,
        parent_capabilities: Optional[Set[str]] = None,
    ) -> SkillResult:
        start = time.perf_counter()
        skill_dir = self._find_skill_dir(skill_name)
        if not skill_dir:
            return SkillResult(
                success=False,
                error=f"Skill '{skill_name}' directory not found",
                duration_ms=(time.perf_counter() - start) * 1000,
            )

        meta = self.parse_metadata(skill_dir)
        if not meta or not meta.is_executable:
            return SkillResult(
                success=False,
                error=f"Skill '{skill_name}' is not an executable skill (missing skill.py or valid metadata)",
                duration_ms=(time.perf_counter() - start) * 1000,
            )

        # Enforce capability inheritance if nested
        if parent_capabilities is not None:
            effective_caps = set(meta.capabilities) & parent_capabilities
            meta.capabilities = list(effective_caps)

        py_file = skill_dir / "skill.py"
        try:
            source = py_file.read_text(encoding="utf-8", errors="replace")

            from kitt.skills.subprocess_sandbox import SubprocessSkillSandbox
            sandbox = SubprocessSkillSandbox(self.runtime, timeout_seconds=self.timeout)
            return sandbox.execute(
                skill_name=skill_name,
                source=source,
                arguments=arguments,
                capabilities=meta.capabilities,
                call_stack=call_stack,
            )
        except Exception as exc:
            dur = (time.perf_counter() - start) * 1000
            return SkillResult(success=False, error=f"Skill '{skill_name}' failed: {exc}", duration_ms=dur)

    def _find_skill_dir(self, skill_name: str) -> Optional[Path]:
        root_dir = getattr(
            self.runtime,
            "canonical_root",
            getattr(self.runtime, "root_path", getattr(self.runtime, "root", Path("."))),
        )
        candidates = [
            Path(root_dir) / ".kitt" / "skills" / skill_name,
            Path.home() / ".kitt" / "skills" / skill_name,
            Path(root_dir) / "skills" / skill_name,
            Path(root_dir) / "plugins" / skill_name / "skills" / skill_name,
        ]
        for c in candidates:
            if c.exists() and c.is_dir():
                return c
        return None
