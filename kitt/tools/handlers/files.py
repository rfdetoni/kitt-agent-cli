"""Filesystem read/write/list tool handlers."""
from __future__ import annotations

import hashlib
import os
import tempfile
from typing import Any, Dict
from kitt.tools.handlers import ToolContext


class ListFilesHandler:
    def execute(self, args: Dict[str, Any], ctx: ToolContext):
        from kitt.tools.registry import ToolResult
        rel = args.get("path", ".")
        is_safe, target, err = ctx.registry.path_policy.validate_path(rel)
        if not is_safe or not target or not target.exists():
            return ToolResult(success=False, output="", error=err or "Access outside workspace denied.")
        files = [str(p.relative_to(ctx.registry.root_path)) for p in target.glob("*") if p.is_file()][:100]
        return ToolResult(success=True, output="\n".join(files))


class ReadFileHandler:
    def execute(self, args: Dict[str, Any], ctx: ToolContext):
        from kitt.tools.registry import ToolResult
        rel = args.get("path", "")
        around_symbol = str(args.get("around_symbol", "") or "")
        if around_symbol and ctx.registry.repository_index is not None:
            symbol_row = ctx.registry.repository_index.find_symbol_location(around_symbol, rel or None)
            if not symbol_row:
                return ToolResult(success=False, output="", error=f"Symbol not found: {around_symbol}")
            rel = symbol_row["path"]
            context_lines = max(0, min(int(args.get("context_lines", 20)), 200))
            args["start_line"] = max(1, int(symbol_row["start_line"]) - context_lines)
            args["end_line"] = int(symbol_row["end_line"]) + context_lines
        is_safe, target, err = ctx.registry.path_policy.validate_path(rel)
        if not is_safe or not target or not target.exists() or not target.is_file():
            return ToolResult(success=False, output="", error=err or "File not found or outside workspace.")
        start = max(1, int(args.get("start_line", 1))) - 1
        requested_end = int(args.get("end_line", start + 200))
        max_lines = 5000
        max_bytes = max(0, int(args.get("max_bytes", 0) or 0))
        end = min(requested_end, start + max_lines)
        out = []
        used_bytes = 0
        truncated = False
        with target.open("r", encoding="utf-8", errors="ignore") as fh:
            for idx, line in enumerate(fh, 1):
                if idx <= start:
                    continue
                if idx > end:
                    break
                line_text = line.rstrip("\n")
                line_bytes = len((("\n" if out else "") + line_text).encode("utf-8"))
                if max_bytes and used_bytes + line_bytes > max_bytes:
                    remaining = max_bytes - used_bytes - (1 if out else 0)
                    if remaining > 0:
                        out.append(line_text.encode("utf-8")[:remaining].decode("utf-8", errors="ignore"))
                    truncated = True
                    break
                out.append(line_text)
                used_bytes += line_bytes
        chunk = "\n".join(out)
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
                "path": rel,
                "start_line": start + 1,
                "end_line": start + len(out),
                "file_size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            },
        )


class WriteFileHandler:
    def execute(self, args: Dict[str, Any], ctx: ToolContext):
        from kitt.tools.registry import ToolResult
        rel = args.get("path", "") or args.get("file", "")
        content = args.get("content", "")
        is_safe, target, err = ctx.registry.path_policy.validate_path(rel)
        if not is_safe or not target:
            return ToolResult(success=False, output="", error=err or "Access outside workspace denied.")
        target.parent.mkdir(parents=True, exist_ok=True)
        expected_hash = args.get("expected_content_hash")
        if expected_hash and target.exists():
            actual_hash = hashlib.sha256(target.read_bytes()).hexdigest()
            if actual_hash != expected_hash:
                return ToolResult(success=False, output="", error="expected_content_hash mismatch.")
        # Atomic replace prevents readers/indexers from seeing partial content.
        fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
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
        ctx.registry._refresh_index([str(target.relative_to(ctx.registry.root_path))])
        new_hash = hashlib.sha256(target.read_bytes()).hexdigest()
        return ToolResult(
            success=True,
            output=f"Successfully wrote {len(content)} bytes to {rel}.",
            metadata={"content_hash": new_hash},
        )
