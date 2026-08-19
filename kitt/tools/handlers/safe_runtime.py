from __future__ import annotations

import json
from typing import Any, Dict

from kitt.tools.handlers import ToolContext, ToolHandler
from kitt.runtime.safe_runtime import SafeRuntime


class SafeRuntimeHandler(ToolHandler):
    """Composite runtime entry point with mandatory principal context."""

    def execute(self, args: Dict[str, Any], ctx: ToolContext) -> Any:
        from kitt.tools.registry import ToolResult

        operation = str(args.get("operation", "")).strip()
        op_args = args.get("arguments", {})
        if not operation:
            return ToolResult(False, "", "Argument 'operation' is required for kitt_runtime")
        if not isinstance(op_args, dict):
            return ToolResult(False, "", "Argument 'arguments' must be an object")

        sec_ctx = ctx.security_context
        if sec_ctx is None:
            return ToolResult(
                False, "",
                "ExecutionSecurityContext is required for kitt_runtime (fail-closed).",
            )
        try:
            sec_ctx.assert_scope(ctx.workspace_id, ctx.conversation_id)
        except PermissionError as exc:
            return ToolResult(False, "", str(exc))

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
            security_context=sec_ctx,
            approval_grant=ctx.approval_grant,
            expected_approval_id=ctx.expected_approval_id,
        )

        event_bus = getattr(ctx.registry, "event_bus", None)
        if event_bus is not None:
            event_bus.publish("RuntimeOperation", {
                "operation": operation,
                "success": res.success,
                "requires_approval": res.requires_approval,
                "duration_ms": res.duration_ms,
                "trace_id": sec_ctx.trace_id,
                "principal_type": sec_ctx.principal_type,
                "principal_id": sec_ctx.principal_id,
            })

        if res.requires_approval:
            return ToolResult(
                success=False,
                output="",
                error=res.error,
                requires_approval=True,
                metadata={
                    "operation": res.operation,
                    "requires_approval": True,
                    "approval_action": res.approval_action,
                    "approval_payload": res.approval_payload,
                    "required_capability": res.required_capability,
                    "duration_ms": res.duration_ms,
                },
            )

        output = json.dumps(res.data, ensure_ascii=False) if isinstance(res.data, (dict, list)) else str(res.data or "")
        return ToolResult(
            success=res.success,
            output=output,
            error=res.error,
            metadata={
                "operation": res.operation,
                "context_handles": res.context_handles,
                "tokens_saved": res.tokens_saved,
                "duration_ms": res.duration_ms,
            },
        )
