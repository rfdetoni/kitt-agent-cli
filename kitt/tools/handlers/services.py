from __future__ import annotations

from typing import Any, Dict

from kitt.tools.handlers import ToolContext
from kitt.tools.handlers.services_core import (
    ArtifactListHandler,
    ArtifactReadHandler,
    ArtifactStoreHandler,
    GoalAddGateHandler,
    GoalCreateHandler,
    HarnessRememberHandler,
    QueueInputHandler,
)
from kitt.tools.handlers.services_core import ChildSpawnHandler as _CoreChildSpawnHandler


class ChildSpawnHandler(_CoreChildSpawnHandler):
    """Child spawn handler with explicit external-backend selection."""

    def execute(self, args: Dict[str, Any], ctx: ToolContext):
        backend = str(args.get("backend") or "").strip().lower()
        if not backend:
            return super().execute(args, ctx)

        from kitt.tools.registry import ToolResult

        mgr = ctx.registry.child_tools or ctx.registry.child_manager
        if not mgr:
            return ToolResult(False, "", "Child manager unavailable.")
        child = mgr.spawn(
            parent_conversation_id=ctx.conversation_id,
            parent_turn_id=ctx.turn_id,
            name=str(args.get("name", "child_task")),
            task=str(args.get("task", "")),
            workspace_id=ctx.workspace_id,
            allowed_paths=args.get("allowed_paths", []),
            enabled_tools=(
                args.get("enabled_tools")
                or args.get("allowed_tools")
                or ["read_file", "search"]
            ),
            token_budget=int(args.get("token_budget", 4000)),
            timeout_seconds=float(args.get("timeout_seconds", 60.0)),
            security_context=ctx.security_context,
            backend=backend,
        )
        return ToolResult(
            True,
            f"Child task spawned with ID {child.id} using backend {backend}.",
            metadata={"child_id": child.id, "child": child, "backend": backend},
        )


__all__ = [
    "ArtifactStoreHandler",
    "ArtifactReadHandler",
    "ArtifactListHandler",
    "QueueInputHandler",
    "GoalCreateHandler",
    "GoalAddGateHandler",
    "ChildSpawnHandler",
    "HarnessRememberHandler",
]
