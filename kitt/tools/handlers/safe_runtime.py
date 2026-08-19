from __future__ import annotations

import json
from typing import Any, Dict
from kitt.tools.handlers import ToolContext, ToolHandler
from kitt.runtime.safe_runtime import SafeRuntime


class SafeRuntimeHandler(ToolHandler):
    """Handler for composite kitt_runtime tool operations."""

    def execute(self, args: Dict[str, Any], ctx: ToolContext) -> Any:
        from kitt.tools.registry import ToolResult

        operation = args.get("operation", "")
        op_args = args.get("arguments", {})
        if not operation:
            return ToolResult(
                success=False,
                output="",
                error="Argument 'operation' is required for kitt_runtime",
            )

        safe_runtime = getattr(ctx.registry, "_safe_runtime_instance", None)
        if safe_runtime is None or safe_runtime.conversation_id != ctx.conversation_id:
            safe_runtime = SafeRuntime(
                workspace_root=ctx.registry.root_path,
                workspace_id=ctx.workspace_id,
                conversation_id=ctx.conversation_id,
                tool_registry=ctx.registry,
                repository_index=ctx.registry.repository_index,
                artifact_store=ctx.registry.artifacts,
                child_manager=ctx.registry.child_manager,
                goal_service=ctx.registry.goal_service,
                memory_service=getattr(ctx.registry, "memory_service", None),
                skill_manager=getattr(ctx.registry, "skill_manager", None),
                db=getattr(ctx.registry, "db", None),
            )
            ctx.registry._safe_runtime_instance = safe_runtime

        res = safe_runtime.execute(
            operation=operation,
            arguments=op_args,
            turn_id=ctx.turn_id,
            origin=ctx.origin,
        )

        output_str = json.dumps(res.data, ensure_ascii=False) if isinstance(res.data, (dict, list)) else str(res.data or "")
        return ToolResult(
            success=res.success,
            output=output_str,
            error=res.error,
            metadata={
                "operation": res.operation,
                "context_handles": res.context_handles,
                "tokens_saved": res.tokens_saved,
                "duration_ms": res.duration_ms,
            },
        )
