"""Filesystem read/write/list tool handlers."""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any, Dict

from kitt.tools.handlers import ToolContext


def _relative_target(ctx: ToolContext, target: Path) -> str:
    relative = str(target.relative_to(ctx.registry.root_path))
    if ctx.security_context is not None:
        ctx.security_context.assert_path_allowed(relative)
    return relative


class ListFilesHandler:
    def execute(self, args: Dict[str, Any], ctx: ToolContext):
        from kitt.tools.registry import ToolResult

        rel = args.get("path", ".")
        is_safe, target, error = ctx.registry.path_policy.validate_path(rel)
        if not is_safe or not target or not target.exists() or not target.is_dir():
            return ToolResult(
                success=False,
                output="",
                error=error or "Directory not found or outside workspace.",
            )

        relative_dir = str(target.relative_to(ctx.registry.root_path))
        security = ctx.security_context
        if (
            security is not None
            and not security.allows_path(relative_dir)
            and not security.is_ancestor_of_allowed_path(relative_dir)
        ):
            return ToolResult(False, "", f"Path '{relative_dir}' is outside the principal path scope")

        files = []
        for path in target.glob("*"):
            if not path.is_file():
                continue
            try:
                relative = _relative_target(ctx, path)
            except PermissionError:
                continue
            files.append(relative)
            if len(files) >= 100:
                break
        return ToolResult(success=True, output="\n".join(files))


class ReadFileHandler:
    def execute(self, args: Dict[str, Any], ctx: ToolContext):
        from kitt.tools.registry import ToolResult

        rel = args.get("path", "")
        around_symbol = str(args.get("around_symbol", "") or "")
        if around_symbol and ctx.registry.repository_index is not None:
            symbol_row = ctx.registry.repository_index.find_symbol_location(
                around_symbol, rel or None
            )
            if not symbol_row:
                return ToolResult(False, "", f"Symbol not found: {around_symbol}")
            rel = symbol_row["path"]
            context_lines = max(0, min(int(args.get("context_lines", 20)), 200))
            args["start_line"] = max(1, int(symbol_row["start_line"]) - context_lines)
            args["end_line"] = int(symbol_row["end_line"]) + context_lines

        is_safe, target, error = ctx.registry.path_policy.validate_path(rel)
        if not is_safe or not target or not target.exists() or not target.is_file():
            return ToolResult(
                success=False,
                output="",
                error=error or "File not found or outside workspace.",
            )
        try:
            relative = _relative_target(ctx, target)
        except PermissionError as exc:
            return ToolResult(False, "", str(exc))

        start_value = args.get("start_line")
        start = max(1, int(start_value if start_value is not None else 1)) - 1
        end_value = args.get("end_line")
        requested_end = int(end_value if end_value is not None else start + 200)
        max_lines = 5000
        max_bytes = max(0, int(args.get("max_bytes", 0) or 0))
        end = min(requested_end, start + max_lines)

        output: list[str] = []
        used_bytes = 0
        truncated = False
        with target.open("r", encoding="utf-8", errors="ignore") as handle:
            for line_number, line in enumerate(handle, 1):
                if line_number <= start:
                    continue
                if line_number > end:
                    break
                line_text = line.rstrip("\n")
                prefix = "\n" if output else ""
                line_bytes = len((prefix + line_text).encode("utf-8"))
                if max_bytes and used_bytes + line_bytes > max_bytes:
                    remaining = max_bytes - used_bytes - (1 if output else 0)
                    if remaining > 0:
                        output.append(
                            line_text.encode("utf-8")[:remaining].decode(
                                "utf-8", errors="ignore"
                            )
                        )
                    truncated = True
                    break
                output.append(line_text)
                used_bytes += line_bytes

        chunk = "\n".join(output)
        stat = target.stat()
        digest = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
        return ToolResult(
            success=True,
            output=chunk,
            truncated=truncated,
            bytes_count=len(chunk.encode("utf-8")),
            metadata={
                "content_hash": digest,
                "hash_scope": "returned_range",
                "path": relative,
                "start_line": start + 1,
                "end_line": start + len(output),
                "file_size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            },
        )


class WriteFileHandler:
    def execute(self, args: Dict[str, Any], ctx: ToolContext):
        from kitt.tools.registry import ToolResult

        rel = args.get("path", "") or args.get("file", "")
        content = args.get("content", "")
        is_safe, target, error = ctx.registry.path_policy.validate_path(rel)
        if not is_safe or not target:
            return ToolResult(
                success=False,
                output="",
                error=error or "Access outside workspace denied.",
            )

        if (
            not target.exists()
            and not target.parent.exists()
            and (ctx.registry.root_path / target.name).is_file()
        ):
            target = ctx.registry.root_path / target.name

        try:
            relative = _relative_target(ctx, target)
        except PermissionError as exc:
            return ToolResult(False, "", str(exc))

        target.parent.mkdir(parents=True, exist_ok=True)
        expected_hash = args.get("expected_content_hash")
        if expected_hash and target.exists():
            actual_hash = hashlib.sha256(target.read_bytes()).hexdigest()
            if actual_hash != expected_hash:
                return ToolResult(False, "", "expected_content_hash mismatch.")

        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.", dir=str(target.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, target)
        except Exception:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
            raise

        ctx.registry._refresh_index([relative])
        new_hash = hashlib.sha256(target.read_bytes()).hexdigest()
        return ToolResult(
            success=True,
            output=f"Successfully wrote {len(content)} bytes to {relative}.",
            metadata={"content_hash": new_hash, "path": relative},
        )
