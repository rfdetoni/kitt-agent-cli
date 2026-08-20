from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Optional

from kitt.security.capabilities import (
    CAP_ARTIFACT_READ,
    CAP_CHILD_INSPECT,
    CAP_GOAL_MANAGE,
    CAP_REPO_READ,
)


class ContextHandleResolver:
    """Resolve compact context handles with bounded, scoped I/O."""

    def __init__(
        self,
        root_dir: str | Path,
        repository_index=None,
        artifact_store=None,
        child_manager=None,
        goal_service=None,
        workspace_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ):
        self.root = Path(root_dir).resolve()
        self.index = repository_index
        self.artifacts = artifact_store
        self.children = child_manager
        self.goals = goal_service
        self.workspace_id = workspace_id
        self.conversation_id = conversation_id

    @staticmethod
    def _path_allowed(security_context, relative_path: str) -> bool:
        return security_context is None or security_context.allows_path(relative_path)

    @staticmethod
    def _require_capability(security_context, capability: str) -> None:
        if security_context is not None:
            security_context.check_capability(capability)

    def resolve(self, handle: str, security_context=None) -> Dict[str, Any]:
        if not isinstance(handle, str) or not handle.strip():
            raise ValueError("Handle must be a non-empty string")
        handle = handle.strip()

        if handle.startswith("ctx:repo:"):
            self._require_capability(security_context, CAP_REPO_READ)
            return self._resolve_repo_symbol(
                handle[len("ctx:repo:") :], security_context=security_context
            )
        if handle.startswith("ctx:file:"):
            self._require_capability(security_context, CAP_REPO_READ)
            return self._resolve_file_slice(
                handle[len("ctx:file:") :], security_context=security_context
            )
        if handle.startswith("artifact:"):
            self._require_capability(security_context, CAP_ARTIFACT_READ)
            return self._resolve_artifact(handle[len("artifact:") :])
        if handle.startswith("child:"):
            self._require_capability(security_context, CAP_CHILD_INSPECT)
            return self._resolve_child(handle[len("child:") :])
        if handle.startswith("goal:"):
            self._require_capability(security_context, CAP_GOAL_MANAGE)
            return self._resolve_goal(handle[len("goal:") :])
        raise ValueError(f"Unknown handle scheme: '{handle}'")

    def _resolve_repo_symbol(self, symbol: str, security_context=None) -> Dict[str, Any]:
        if not symbol:
            raise ValueError("Symbol name cannot be empty")
        symbols = (
            self.index.symbols_for_symbol(symbol)
            if self.index and hasattr(self.index, "symbols_for_symbol")
            else []
        )
        symbols = [
            item
            for item in symbols
            if self._path_allowed(security_context, str(item.get("path", "")))
        ]
        return {
            "handle": f"ctx:repo:{symbol}",
            "kind": "symbol",
            "count": len(symbols),
            "symbols": symbols[:10],
        }

    def _resolve_file_slice(self, spec: str, security_context=None) -> Dict[str, Any]:
        match = re.match(r"^(.+?)(?::(\d+)(?:-(\d+))?)?$", spec)
        if not match:
            raise ValueError(f"Invalid file handle spec: '{spec}'")
        rel_path, start_s, end_s = match.groups()
        target = (self.root / rel_path).resolve()
        try:
            relative = str(target.relative_to(self.root))
        except ValueError as exc:
            raise ValueError(f"Path escape blocked for handle: '{rel_path}'") from exc
        if security_context is not None:
            security_context.assert_path_allowed(relative)
        if not target.is_file():
            raise FileNotFoundError(f"File not found for handle: '{rel_path}'")

        start = max(1, int(start_s) if start_s else 1)
        requested_end = int(end_s) if end_s else (start + 50 if start_s else 100)
        end = max(start, min(start + 500, requested_end))
        selected: list[str] = []
        bytes_out = 0
        max_bytes = 256 * 1024
        with target.open("r", encoding="utf-8", errors="replace") as stream:
            for line_number, line in enumerate(stream, start=1):
                if line_number < start:
                    continue
                if line_number > end:
                    break
                encoded = line.encode("utf-8", errors="replace")
                if bytes_out + len(encoded) > max_bytes:
                    break
                selected.append(line)
                bytes_out += len(encoded)
        actual_end = start + max(0, len(selected) - 1)
        return {
            "handle": f"ctx:file:{spec}",
            "kind": "file_slice",
            "path": relative,
            "start_line": start,
            "end_line": actual_end,
            "content": "".join(selected),
            "total_lines": None,
            "bounded": True,
            "bytes_returned": bytes_out,
        }

    def _resolve_artifact(self, artifact_id: str) -> Dict[str, Any]:
        if not self.artifacts:
            raise ValueError("ArtifactStore not attached")
        artifact = self.artifacts.get(artifact_id)
        if not artifact:
            raise KeyError(f"Artifact '{artifact_id}' not found")
        if self.workspace_id and artifact.workspace_id != self.workspace_id:
            raise PermissionError("Cross-workspace artifact access denied")
        if self.conversation_id and artifact.conversation_id not in (
            None,
            self.conversation_id,
        ):
            raise PermissionError("Cross-conversation artifact access denied")
        page = self.artifacts.read_text_page(artifact_id, offset=0, max_bytes=32768)
        return {
            "handle": f"artifact:{artifact_id}",
            "kind": "artifact",
            "artifact_id": artifact_id,
            "type": artifact.artifact_type,
            "summary": artifact.summary,
            "content": page["content"],
            "has_more": page["has_more"],
            "total_bytes": page["total_bytes"],
        }

    def _resolve_child(self, child_id: str) -> Dict[str, Any]:
        if not self.children:
            raise ValueError("ChildAgentManager not attached")
        child = self.children.inspect(
            child_id,
            conversation_id=self.conversation_id,
            workspace_id=self.workspace_id,
        )
        if not child:
            raise KeyError(f"Child session '{child_id}' not found")
        return {
            "handle": f"child:{child_id}",
            "kind": "child",
            "child_id": child_id,
            "name": child.name,
            "state": child.state,
            "result_artifact_id": child.result_artifact_id,
            "error": child.error,
        }

    def _resolve_goal(self, goal_id: str) -> Dict[str, Any]:
        if not self.goals:
            raise ValueError("GoalService not attached")
        goal = (
            self.goals.get_scoped(goal_id, self.conversation_id)
            if self.conversation_id
            else self.goals.get(goal_id)
        )
        if not goal:
            raise KeyError(f"Goal '{goal_id}' not found")
        return {
            "handle": f"goal:{goal_id}",
            "kind": "goal",
            "goal_id": goal_id,
            "objective": goal.objective,
            "state": goal.state,
            "turns_used": goal.turns_used,
            "tokens_used": goal.tokens_used,
        }
