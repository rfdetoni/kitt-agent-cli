"""Race-resistant filesystem read/write/list tool handlers."""
from __future__ import annotations

import hashlib
from typing import Any, Dict

from kitt.security.workspace_fs import DEFAULT_MAX_FILE_BYTES, WorkspaceFileSystem
from kitt.tools.handlers import ToolContext


def _fs(ctx: ToolContext) -> WorkspaceFileSystem:
    return WorkspaceFileSystem(ctx.registry.root_path, max_file_bytes=DEFAULT_MAX_FILE_BYTES)


def _scope(ctx: ToolContext, relative: str) -> str:
    if ctx.security_context is not None:
        ctx.security_context.assert_path_allowed(relative)
    return relative


class ListFilesHandler:
    def execute(self, args: Dict[str, Any], ctx: ToolContext):
        from kitt.tools.registry import ToolResult
        rel = str(args.get("path", ".") or ".")
        try:
            fs = _fs(ctx)
            relative_dir = fs.relative(rel)
            security = ctx.security_context
            if (
                security is not None
                and not security.allows_path(relative_dir)
                and not security.is_ancestor_of_allowed_path(relative_dir)
            ):
                return ToolResult(False, "", f"Path '{relative_dir}' is outside the principal path scope")
            files = []
            for item in fs.list_regular_files(rel, limit=100):
                try:
                    files.append(_scope(ctx, item))
                except PermissionError:
                    continue
            return ToolResult(True, "\n".join(files))
        except Exception as exc:
            return ToolResult(False, "", f"Directory access denied: {exc}")


class ReadFileHandler:
    def execute(self, args: Dict[str, Any], ctx: ToolContext):
        from kitt.tools.registry import ToolResult

        rel = str(args.get("path", "") or "")
        around_symbol = str(args.get("around_symbol", "") or "")
        if around_symbol and ctx.registry.repository_index is not None:
            symbol = ctx.registry.repository_index.find_symbol_location(around_symbol, rel or None)
            if not symbol:
                return ToolResult(False, "", f"Symbol not found: {around_symbol}")
            rel = symbol["path"]
            context_lines = max(0, min(int(args.get("context_lines", 20)), 200))
            args["start_line"] = max(1, int(symbol["start_line"]) - context_lines)
            args["end_line"] = int(symbol["end_line"]) + context_lines

        try:
            fs = _fs(ctx)
            relative = _scope(ctx, fs.relative(rel))
            requested_max = int(args.get("max_bytes", 0) or 0)
            data = fs.read(rel, max_bytes=DEFAULT_MAX_FILE_BYTES)
        except Exception as exc:
            return ToolResult(False, "", f"File access denied: {exc}")

        start_value = args.get("start_line")
        start = max(1, int(start_value if start_value is not None else 1)) - 1
        end_value = args.get("end_line")
        requested_end = int(end_value if end_value is not None else start + 200)
        end = min(requested_end, start + 5000)
        text = data.content.decode("utf-8", errors="ignore")
        lines = text.splitlines()
        selected = lines[start:end]
        chunk = "\n".join(selected)

        if requested_max > 0:
            encoded = chunk.encode("utf-8")
            if len(encoded) > requested_max:
                encoded = encoded[:requested_max]
                chunk = encoded.decode("utf-8", errors="ignore")
                truncated = True
            else:
                truncated = False
        else:
            truncated = end < len(lines)

        return ToolResult(
            True,
            chunk,
            bytes_count=len(chunk.encode("utf-8")),
            truncated=truncated,
            metadata={
                "content_hash": hashlib.sha256(chunk.encode("utf-8")).hexdigest(),
                "hash_scope": "returned_range",
                "path": relative,
                "start_line": start + 1,
                "end_line": start + len(selected),
                "file_size": data.size,
                "mtime_ns": data.mtime_ns,
                "full_file_hash": data.sha256,
            },
        )


class WriteFileHandler:
    def execute(self, args: Dict[str, Any], ctx: ToolContext):
        from kitt.tools.registry import ToolResult

        rel = str(args.get("path", "") or args.get("file", "") or "")
        content = args.get("content", "")
        if not isinstance(content, str):
            return ToolResult(False, "", "write_file content must be a string")
        if len(content.encode("utf-8")) > DEFAULT_MAX_FILE_BYTES:
            return ToolResult(False, "", f"write_file content exceeds {DEFAULT_MAX_FILE_BYTES} bytes")

        fs = _fs(ctx)
        try:
            relative = _scope(ctx, fs.relative(rel))
            try:
                before = fs.read(relative)
                existed = True
                before_hash = before.sha256
                before_content = before.content.decode("utf-8")
            except FileNotFoundError:
                existed = False
                before_hash = None
                before_content = None

            supplied_hash = args.get("expected_content_hash")
            if supplied_hash is not None and supplied_hash != before_hash:
                return ToolResult(False, "", "expected_content_hash mismatch")

            from kitt.domain.entities import FileSnapshot
            from kitt.edit_format.transaction import workspace_mutation_lock
            with workspace_mutation_lock(ctx.registry.root_path):
                digest = fs.atomic_write(
                    relative, content,
                    expected_exists=existed,
                    expected_sha256=before_hash if existed else None,
                    max_bytes=DEFAULT_MAX_FILE_BYTES,
                )
                try:
                    changeset = ctx.registry.applier.tracker.record_changeset(
                        description=f"write_file {relative}",
                        snapshots=[FileSnapshot(relative, existed, before_content)],
                        workspace_id=ctx.workspace_id,
                        conversation_id=ctx.conversation_id,
                        turn_id=ctx.turn_id,
                        post_hashes={relative: digest},
                        post_exists={relative: True},
                        post_contents={relative: content},
                    )
                except Exception:
                    if existed:
                        fs.atomic_write(
                            relative, before_content or "",
                            expected_exists=True, expected_sha256=digest,
                        )
                    else:
                        fs.unlink(relative, expected_exists=True, expected_sha256=digest)
                    raise
        except Exception as exc:
            return ToolResult(False, "", f"Workspace write refused: {exc}")

        ctx.registry._refresh_index([relative])
        return ToolResult(
            True,
            f"Successfully wrote {len(content.encode('utf-8'))} bytes to {relative}.",
            metadata={"content_hash": digest, "path": relative, "changeset": changeset},
        )
