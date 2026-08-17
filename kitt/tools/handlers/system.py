"""System, execution, patch, and git tool handlers."""
from __future__ import annotations

import shlex
from typing import Any, Dict
from kitt.tools.handlers import ToolContext


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
        edit_res = ctx.registry.applier.apply(blocks, root_dir=str(ctx.registry.root_path), allow_overwrite_existing=True)
        if edit_res.success:
            ctx.registry._refresh_index(edit_res.applied_files + edit_res.created_files)
            output = f"Applied edit to {len(edit_res.applied_files + edit_res.created_files)} file(s)."
            return ToolResult(success=True, output=output, metadata={"edit_result": edit_res})
        return ToolResult(success=False, output="", error="\n".join(edit_res.errors), metadata={"edit_result": edit_res})


class RunCommandHandler:
    def execute(self, args: Dict[str, Any], ctx: ToolContext):
        from kitt.tools.registry import ToolResult
        cmd_str = str(args.get("command", "")).strip()
        if not cmd_str:
            return ToolResult(success=False, output="", error="Empty command.")
        try:
            argv = shlex.split(cmd_str)
        except Exception as se:
            return ToolResult(success=False, output="", error=f"Invalid shell command syntax: {se}")
        res = ctx.registry.process_runner.run(argv, timeout_seconds=30)
        output = res.stdout + (("\n" + res.stderr) if res.stderr else "")
        return ToolResult(
            success=(res.returncode == 0 and not res.timed_out),
            output=output,
            error=None if res.returncode == 0 else res.stderr,
            bytes_count=len(output.encode()),
            truncated=res.truncated,
        )


class GitStatusHandler:
    def execute(self, args: Dict[str, Any], ctx: ToolContext):
        from kitt.tools.registry import ToolResult
        res = ctx.registry.process_runner.run(["git", "status", "--short"], timeout_seconds=30)
        return ToolResult(success=res.returncode == 0, output=res.stdout, error=res.stderr or None)


class GitDiffHandler:
    def execute(self, args: Dict[str, Any], ctx: ToolContext):
        from kitt.tools.registry import ToolResult
        res = ctx.registry.process_runner.run(["git", "diff"], timeout_seconds=30)
        return ToolResult(success=res.returncode == 0, output=res.stdout, error=res.stderr or None)
