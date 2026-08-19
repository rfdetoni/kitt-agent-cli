from __future__ import annotations

import importlib.util
import sys
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

    def search_repo(self, pattern: str, regex: bool = False) -> Any:
        self._require_capability(CAP_REPO_SEARCH)
        return self.runtime.execute("repo.search", {"pattern": pattern, "regex": regex}).data

    def read_file(self, path: str, start_line: int = 1, end_line: int = 100) -> str:
        self._require_capability(CAP_REPO_READ)
        res = self.runtime.execute("repo.read", {"path": path, "start_line": start_line, "end_line": end_line})
        return str(res.data or "")

    def inspect_symbol(self, symbol: str) -> Any:
        self._require_capability(CAP_REPO_READ)
        return self.runtime.execute("repo.inspect_symbol", {"symbol": symbol}).data

    def store_artifact(self, content: str, artifact_type: str = "SKILL_OUTPUT", summary: str = "") -> str:
        self._require_capability(CAP_ARTIFACT_WRITE)
        res = self.runtime.execute(
            "artifacts.store",
            {"content": content, "artifact_type": artifact_type, "summary": summary},
        )
        return str(res.data or "")

    def read_artifact(self, artifact_id: str) -> str:
        self._require_capability(CAP_ARTIFACT_READ)
        res = self.runtime.execute("artifacts.read", {"artifact_id": artifact_id})
        return str(res.data or "")

    def apply_patch(self, patch: str) -> Any:
        self._require_capability(CAP_REPO_WRITE)
        return self.runtime.execute("patch.apply", {"patch": patch}).data

    def run_command(self, command: str) -> Any:
        self._require_capability(CAP_PROCESS_RUN)
        return self.runtime.execute("process.run", {"command": command}).data

    def spawn_child(self, name: str, task: str, allowed_paths: Optional[List[str]] = None) -> Any:
        self._require_capability(CAP_CHILD_SPAWN)
        return self.runtime.execute(
            "children.spawn",
            {"name": name, "task": task, "allowed_paths": allowed_paths or []},
        ).data

    def call_skill(self, skill_name: str, arguments: Dict[str, Any]) -> SkillResult:
        if len(self.call_stack) >= self.max_depth:
            raise RuntimeError(f"Maximum skill call depth ({self.max_depth}) exceeded")
        if skill_name in self.call_stack:
            raise RuntimeError(f"Skill cycle detected: '{skill_name}' is already in execution stack {self.call_stack}")

        new_stack = set(self.call_stack)
        new_stack.add(skill_name)
        runner = ExecutableSkillRunner(self.runtime)
        return runner.execute(skill_name, arguments, call_stack=new_stack)


class ExecutableSkill(Protocol):
    metadata: ExecutableSkillMetadata

    def execute(self, context: SkillExecutionContext, arguments: Dict[str, Any]) -> Any:
        ...


class ExecutableSkillRunner:
    """Discovers, loads, and securely runs executable skills."""

    def __init__(self, runtime: Any, timeout_seconds: float = 30.0):
        self.runtime = runtime
        self.timeout = timeout_seconds

    def parse_metadata(self, skill_dir: Path) -> Optional[ExecutableSkillMetadata]:
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            return None

        import re
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

        # Validate capabilities against known set
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

        py_file = skill_dir / "skill.py"
        try:
            mod_name = f"kitt_skill_{skill_name}_{int(time.time()*1000)}"
            spec = importlib.util.spec_from_file_location(mod_name, str(py_file))
            if not spec or not spec.loader:
                raise ImportError(f"Could not load spec for {py_file}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            handler = getattr(module, "execute", None) or getattr(module, "main", None)
            if not callable(handler):
                raise AttributeError(f"Skill '{skill_name}/skill.py' must define an 'execute(context, arguments)' function")

            ctx = SkillExecutionContext(meta, self.runtime, call_stack=call_stack)
            res = handler(ctx, arguments)
            dur = (time.perf_counter() - start) * 1000
            return SkillResult(success=True, data=res, duration_ms=dur)
        except Exception as exc:
            dur = (time.perf_counter() - start) * 1000
            return SkillResult(success=False, error=f"Skill '{skill_name}' failed: {exc}", duration_ms=dur)

    def _find_skill_dir(self, skill_name: str) -> Optional[Path]:
        candidates = [
            Path(self.runtime.root) / ".kitt" / "skills" / skill_name,
            Path.home() / ".kitt" / "skills" / skill_name,
            Path(self.runtime.root) / "skills" / skill_name,
            Path(self.runtime.root) / "plugins" / skill_name / "skills" / skill_name,
        ]
        for c in candidates:
            if c.exists() and c.is_dir():
                return c
        return None
