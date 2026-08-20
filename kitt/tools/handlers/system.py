"""System, execution, patch, and git tool handlers."""
from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any, Dict

from kitt.tools.handlers import ToolContext


PATCH_INTEGRITY_KEY = "__kitt_patch_integrity__"


def _scoped_relative_path(ctx: ToolContext, relative_path: str) -> str:
    is_safe, target, error = ctx.registry.path_policy.validate_path(relative_path)
    if not is_safe or not target:
        raise PermissionError(error or "Access outside workspace denied.")
    resolved_relative = str(target.relative_to(ctx.registry.root_path))
    if ctx.security_context is not None:
        ctx.security_context.assert_path_allowed(resolved_relative)
    return resolved_relative


class PythonComputeHandler:
    def execute(self, args: Dict[str, Any], ctx: ToolContext):
        from kitt.tools.registry import ToolResult

        execution = ctx.registry.safe_python.execute(
            code=args.get("code", ""),
            inputs=args.get("inputs", {}),
            result_var=args.get("result_var", "_result"),
        )
        return ToolResult(
            success=execution.success,
            output=execution.output,
            error=execution.error,
            bytes_count=len(execution.output.encode("utf-8")),
            truncated=execution.truncated,
        )


class ApplyPatchHandler:
    def execute(self, args: Dict[str, Any], ctx: ToolContext):
        from kitt.tools.registry import ToolResult

        patch_text = args.get("patch", "")
        blocks = ctx.registry.parser.parse(patch_text)
        if not blocks:
            return ToolResult(False, "", "No valid SEARCH/REPLACE blocks found.")

        try:
            resolved_paths = {
                block.file_path: _scoped_relative_path(ctx, block.file_path)
                for block in blocks
            }
        except PermissionError as exc:
            return ToolResult(False, "", str(exc))

        integrity = args.get(PATCH_INTEGRITY_KEY)
        if integrity is not None:
            if not isinstance(integrity, dict):
                return ToolResult(False, "", "Invalid patch integrity manifest.")
            import hashlib
            for requested_path, relative in resolved_paths.items():
                if relative not in integrity:
                    return ToolResult(False, "", f"Missing integrity record for {relative}.")
                expected = integrity[relative]
                target = ctx.registry.root_path / relative
                if expected is None:
                    if target.exists():
                        return ToolResult(False, "", f"File {relative} appeared after approval request.")
                    continue
                if not target.is_file():
                    return ToolResult(False, "", f"File {relative} disappeared after approval request.")
                actual = hashlib.sha256(target.read_bytes()).hexdigest()
                if actual != expected:
                    return ToolResult(False, "", f"File {relative} changed after approval request.")

        coordinator = getattr(ctx.registry, "coordinator", None)
        security = ctx.security_context
        if coordinator is not None and security is not None and getattr(security, "principal_type", "") == "CHILD":
            try:
                coordinator.claim_paths(resolved_paths.values(), security.principal_id, f"patch turn {ctx.turn_id}")
            except Exception as exc:
                return ToolResult(False, "", f"Coordination conflict: {exc}")

        edit_result = ctx.registry.applier.apply(
            blocks,
            root_dir=str(ctx.registry.root_path),
            allow_overwrite_existing=True,
        )
        if edit_result.success:
            changed = edit_result.applied_files + edit_result.created_files
            ctx.registry._refresh_index(changed)
            return ToolResult(
                success=True,
                output=f"Applied edit to {len(changed)} file(s).",
                metadata={"edit_result": edit_result},
            )
        return ToolResult(
            success=False,
            output="",
            error="\n".join(edit_result.errors),
            metadata={"edit_result": edit_result},
        )


class RunCommandHandler:
    def execute(self, args: Dict[str, Any], ctx: ToolContext):
        from kitt.tools.registry import ToolResult

        if ctx.security_context is not None and ctx.security_context.is_path_scoped:
            return ToolResult(
                False,
                "",
                "run_command is denied for path-scoped principals because subprocess filesystem access cannot be safely confined.",
            )

        command = str(args.get("command", "")).strip()
        if not command:
            return ToolResult(False, "", "Empty command.")
        try:
            argv = shlex.split(command)
        except Exception as exc:
            return ToolResult(False, "", f"Invalid shell command syntax: {exc}")

        result = ctx.registry.process_runner.run(argv, timeout_seconds=30)
        optimizer = getattr(ctx.registry, "output_optimizer", None)
        if optimizer is not None:
            optimized = optimizer.optimize(
                argv, result.stdout, result.stderr, result.returncode,
                artifact_store=getattr(ctx.registry, "artifacts", None),
                workspace_id=ctx.workspace_id, conversation_id=ctx.conversation_id, turn_id=ctx.turn_id,
            )
            output = optimized.output
            metadata = {
                "output_family": optimized.family,
                "raw_bytes": optimized.raw_bytes,
                "optimized_bytes": optimized.output_bytes,
                "omitted_lines": optimized.omitted_lines,
                "raw_artifact_id": optimized.raw_artifact_id,
            }
        else:
            output = result.stdout + (("\n" + result.stderr) if result.stderr else "")
            metadata = {}
        return ToolResult(
            success=(result.returncode == 0 and not result.timed_out),
            output=output,
            error=None if result.returncode == 0 else f"Command exited with code {result.returncode}",
            bytes_count=len(output.encode()),
            truncated=result.truncated,
            metadata=metadata,
        )


def _git_pathspec_args(ctx: ToolContext) -> list[str]:
    security = ctx.security_context
    if security is None or security.path_scope is None:
        return []
    return ["--", *sorted(security.path_scope)]


class GitStatusHandler:
    def execute(self, args: Dict[str, Any], ctx: ToolContext):
        from kitt.tools.registry import ToolResult

        argv = ["git", "status", "--short", *_git_pathspec_args(ctx)]
        result = ctx.registry.process_runner.run(argv, timeout_seconds=30)
        return ToolResult(
            success=result.returncode == 0,
            output=result.stdout,
            error=result.stderr or None,
        )


class GitDiffHandler:
    def execute(self, args: Dict[str, Any], ctx: ToolContext):
        from kitt.tools.registry import ToolResult

        argv = ["git", "diff", *_git_pathspec_args(ctx)]
        result = ctx.registry.process_runner.run(argv, timeout_seconds=30)
        return ToolResult(
            success=result.returncode == 0,
            output=result.stdout,
            error=result.stderr or None,
        )
