from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Optional


class ContextHandleResolver:
    """Resolves and validates compact context handles (ctx:..., artifact:..., child:..., goal:...)."""

    def __init__(self, root_dir: str | Path, repository_index=None, artifact_store=None,
                 child_manager=None, goal_service=None, workspace_id: Optional[str] = None,
                 conversation_id: Optional[str] = None):
        self.root = Path(root_dir).resolve()
        self.index = repository_index
        self.artifacts = artifact_store
        self.children = child_manager
        self.goals = goal_service
        self.workspace_id = workspace_id
        self.conversation_id = conversation_id

    def resolve(self, handle: str) -> Dict[str, Any]:
        """Resolve a handle string into structured content."""
        if not isinstance(handle, str) or not handle.strip():
            raise ValueError("Handle must be a non-empty string")
        handle = handle.strip()

        if handle.startswith("ctx:repo:"):
            symbol = handle[len("ctx:repo:"):]
            return self._resolve_repo_symbol(symbol)
        elif handle.startswith("ctx:file:"):
            spec = handle[len("ctx:file:"):]
            return self._resolve_file_slice(spec)
        elif handle.startswith("artifact:"):
            art_id = handle[len("artifact:"):]
            return self._resolve_artifact(art_id)
        elif handle.startswith("child:"):
            child_id = handle[len("child:"):]
            return self._resolve_child(child_id)
        elif handle.startswith("goal:"):
            goal_id = handle[len("goal:"):]
            return self._resolve_goal(goal_id)
        else:
            raise ValueError(f"Unknown handle scheme: '{handle}'")

    def _resolve_repo_symbol(self, symbol: str) -> Dict[str, Any]:
        if not symbol:
            raise ValueError("Symbol name cannot be empty")
        if self.index is not None and hasattr(self.index, "symbols_for_symbol"):
            syms = self.index.symbols_for_symbol(symbol)
            if syms:
                return {
                    "handle": f"ctx:repo:{symbol}",
                    "kind": "symbol",
                    "count": len(syms),
                    "symbols": syms[:10],
                }
        return {"handle": f"ctx:repo:{symbol}", "kind": "symbol", "count": 0, "symbols": []}

    def _resolve_file_slice(self, spec: str) -> Dict[str, Any]:
        # format: path:start-end or path:line
        match = re.match(r"^(.+?)(?::(\d+)(?:-(\d+))?)?$", spec)
        if not match:
            raise ValueError(f"Invalid file handle spec: '{spec}'")
        rel_path, start_s, end_s = match.groups()
        target = (self.root / rel_path).resolve()
        try:
            target.relative_to(self.root)
        except ValueError:
            raise ValueError(f"Path escape blocked for handle: '{rel_path}'")
        if not target.exists() or not target.is_file():
            raise FileNotFoundError(f"File not found for handle: '{rel_path}'")

        start = int(start_s) if start_s else 1
        end = int(end_s) if end_s else (start + 50 if start_s else 100)
        start = max(1, start)
        end = max(start, min(start + 500, end))

        with open(target, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        slice_lines = lines[start - 1 : end]
        return {
            "handle": f"ctx:file:{spec}",
            "kind": "file_slice",
            "path": rel_path,
            "start_line": start,
            "end_line": min(len(lines), end),
            "content": "".join(slice_lines),
            "total_lines": len(lines),
        }

    def _resolve_artifact(self, artifact_id: str) -> Dict[str, Any]:
        if not self.artifacts:
            raise ValueError("ArtifactStore not attached")
        art = self.artifacts.get(artifact_id)
        if not art:
            raise KeyError(f"Artifact '{artifact_id}' not found")
        if self.workspace_id and getattr(art, "workspace_id", None) and art.workspace_id != self.workspace_id:
            raise PermissionError(f"Cross-workspace artifact access denied for '{artifact_id}'")
        if self.conversation_id and getattr(art, "conversation_id", None) and art.conversation_id != self.conversation_id:
            raise PermissionError(f"Cross-conversation artifact access denied for '{artifact_id}'")
        content = self.artifacts.read_text(artifact_id, limit=2000)
        return {
            "handle": f"artifact:{artifact_id}",
            "kind": "artifact",
            "artifact_id": artifact_id,
            "type": getattr(art, "artifact_type", "UNKNOWN"),
            "summary": getattr(art, "summary", ""),
            "content": content,
        }

    def _resolve_child(self, child_id: str) -> Dict[str, Any]:
        if not self.children:
            raise ValueError("ChildAgentManager not attached")
        child = self.children.inspect(child_id)
        if not child:
            raise KeyError(f"Child session '{child_id}' not found")
        if self.workspace_id and getattr(child, "workspace_id", None) and child.workspace_id != self.workspace_id:
            raise PermissionError(f"Cross-workspace child access denied for '{child_id}'")
        if self.conversation_id and getattr(child, "parent_conversation_id", None) and child.parent_conversation_id != self.conversation_id:
            raise PermissionError(f"Cross-conversation child access denied for '{child_id}'")
        return {
            "handle": f"child:{child_id}",
            "kind": "child",
            "child_id": child_id,
            "name": getattr(child, "name", ""),
            "state": getattr(child, "state", ""),
            "result_artifact_id": getattr(child, "result_artifact_id", None),
            "error": getattr(child, "error", None),
        }

    def _resolve_goal(self, goal_id: str) -> Dict[str, Any]:
        if not self.goals:
            raise ValueError("GoalService not attached")
        goal = self.goals.get(goal_id)
        if not goal:
            raise KeyError(f"Goal '{goal_id}' not found")
        if self.conversation_id and getattr(goal, "conversation_id", None) and goal.conversation_id != self.conversation_id:
            raise PermissionError(f"Cross-conversation goal access denied for '{goal_id}'")
        return {
            "handle": f"goal:{goal_id}",
            "kind": "goal",
            "goal_id": goal_id,
            "objective": getattr(goal, "objective", ""),
            "state": getattr(goal, "state", ""),
            "turns_used": getattr(goal, "turns_used", 0),
            "tokens_used": getattr(goal, "tokens_used", 0),
        }
