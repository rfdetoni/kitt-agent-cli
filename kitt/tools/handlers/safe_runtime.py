from __future__ import annotations

import hashlib
import json
from typing import Any, Dict

from kitt.core.pending_action import embed_resume_descriptor
from kitt.runtime.safe_runtime import SafeRuntime
from kitt.tools.handlers import ToolContext, ToolHandler
from kitt.tools.handlers.system import PATCH_INTEGRITY_KEY


class SafeRuntimeHandler(ToolHandler):
    """Single compact model-facing entry point for SafeRuntime operations."""

    @staticmethod
    def _patch_integrity_metadata(ctx: ToolContext, payload: dict) -> tuple[list[str], dict[str, str]]:
        patch_text = str(payload.get("patch", ""))
        blocks = ctx.registry.parser.parse(patch_text)
        affected: list[str] = []
        before_hashes: dict[str, str] = {}
        integrity_manifest: dict[str, str | None] = {}
        for block in blocks:
            is_safe, target, error = ctx.registry.path_policy.validate_path(block.file_path)
            if not is_safe or not target:
                raise PermissionError(error or "Patch path is outside workspace")
            relative = str(target.relative_to(ctx.registry.root_path))
            if ctx.security_context is not None:
                ctx.security_context.assert_path_allowed(relative)
            affected.append(relative)
            if target.exists() and target.is_file():
                digest = hashlib.sha256(target.read_bytes()).hexdigest()
                before_hashes[relative] = digest
                integrity_manifest[relative] = digest
            else:
                before_hashes[relative] = "__MISSING__"
                integrity_manifest[relative] = None
        payload[PATCH_INTEGRITY_KEY] = integrity_manifest
        return affected, before_hashes

    def execute(self, args: Dict[str, Any], ctx: ToolContext) -> Any:
        from kitt.tools.registry import ToolResult

        operation = str(args.get("operation", "")).strip()
        operation_args = args.get("arguments", {})
        if not operation:
            return ToolResult(False, "", "Argument 'operation' is required for kitt_runtime")
        if not isinstance(operation_args, dict):
            return ToolResult(False, "", "Argument 'arguments' must be an object")

        security_context = ctx.security_context
        if security_context is None:
            return ToolResult(
                False,
                "",
                "ExecutionSecurityContext is required for kitt_runtime (fail-closed).",
            )
        try:
            security_context.assert_scope(ctx.workspace_id, ctx.conversation_id)
        except PermissionError as exc:
            return ToolResult(False, "", str(exc))

        safe_runtime = getattr(ctx.registry, "_safe_runtime_instance", None)
        if (
            safe_runtime is None
            or safe_runtime.conversation_id != ctx.conversation_id
            or safe_runtime.workspace_id != ctx.workspace_id
        ):
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

        result = safe_runtime.execute(
            operation=operation,
            arguments=operation_args,
            turn_id=ctx.turn_id,
            origin=ctx.origin,
            security_context=security_context,
            approval_grant=ctx.approval_grant,
            expected_approval_id=ctx.expected_approval_id,
        )

        event_bus = getattr(ctx.registry, "event_bus", None)
        if event_bus is not None:
            event_bus.publish(
                "RuntimeOperation",
                {
                    "operation": operation,
                    "success": result.success,
                    "requires_approval": result.requires_approval,
                    "duration_ms": result.duration_ms,
                    "trace_id": security_context.trace_id,
                    "principal_type": security_context.principal_type,
                    "principal_id": security_context.principal_id,
                },
            )

        if result.requires_approval:
            approval_payload = result.approval_payload or dict(operation_args)
            affected_paths: list[str] = []
            before_hashes: dict[str, str] = {}
            if result.resume_tool_name == "apply_patch":
                try:
                    affected_paths, before_hashes = self._patch_integrity_metadata(
                        ctx, approval_payload
                    )
                except PermissionError as exc:
                    return ToolResult(False, "", str(exc))

            if result.resume_tool_name:
                embed_resume_descriptor(
                    args,
                    tool_name=result.resume_tool_name,
                    arguments=approval_payload,
                    affected_paths=affected_paths,
                    before_hashes=before_hashes,
                )

            return ToolResult(
                success=False,
                output="",
                error=result.error,
                requires_approval=True,
                metadata={
                    "operation": result.operation,
                    "requires_approval": True,
                    "approval_action": result.approval_action,
                    "approval_payload": approval_payload,
                    "required_capability": result.required_capability,
                    "resume_tool_name": result.resume_tool_name,
                    "affected_paths": affected_paths,
                    "before_hashes": before_hashes,
                    "duration_ms": result.duration_ms,
                },
            )

        metadata = {
            "operation": result.operation,
            "context_handles": result.context_handles,
            "tokens_saved": result.tokens_saved,
            "duration_ms": result.duration_ms,
            **(result.metadata or {}),
        }
        edit_result = metadata.get("edit_result")
        if result.success and edit_result is not None:
            ctx.registry.record_edit_result(
                conversation_id=ctx.conversation_id,
                turn_id=ctx.turn_id,
                edit_result=edit_result,
                kind="kitt_runtime",
            )

        output = (
            json.dumps(result.data, ensure_ascii=False)
            if isinstance(result.data, (dict, list))
            else str(result.data or "")
        )
        return ToolResult(
            success=result.success,
            output=output,
            error=result.error,
            metadata=metadata,
        )
