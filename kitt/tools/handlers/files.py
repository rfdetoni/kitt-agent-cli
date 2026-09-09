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


def _token_budget(args: Dict[str, Any], default: int) -> int:
    try:
        value = int(args.get("max_tokens", default) or default)
    except (TypeError, ValueError):
        value = default
    return max(64, min(value, 32_000))


class ListFilesHandler:
    def execute(self, args: Dict[str, Any], ctx: ToolContext):
        from kitt.tools.registry import ToolResult

        rel = str(args.get("path", ".") or ".")
        try:
            limit = max(1, min(int(args.get("limit", 100) or 100), 500))
        except (TypeError, ValueError):
            limit = 100
        max_tokens = _token_budget(args, 600)

        try:
            fs = _fs(ctx)
            relative_dir = fs.relative(rel)
            security = ctx.security_context
            if (
                security is not None
                and not security.allows_path(relative_dir)
                and not security.is_ancestor_of_allowed_path(relative_dir)
            ):
                return ToolResult(
                    False,
                    "",
                    f"Path '{relative_dir}' is outside the principal path scope",
                )

            # WorkspaceFileSystem is the canonical filesystem trust boundary.
            # Do not bypass its dir_fd/O_NOFOLLOW/reparse checks for a native
            # fast path. Scan one extra visible candidate to report truncation
            # without enumerating the whole repository into model context.
            scan_limit = 500 if security is not None and security.is_path_scoped else min(500, limit + 1)
            scanned = fs.list_regular_files(rel, limit=scan_limit)

            visible: list[str] = []
            for item in scanned:
                try:
                    visible.append(_scope(ctx, item))
                except PermissionError:
                    continue

            bounded_candidates = visible[:limit]
            files: list[str] = []
            char_budget = max_tokens * 4
            chars = 0
            for item in bounded_candidates:
                extra = len(item.encode("utf-8")) + (1 if files else 0)
                if files and chars + extra > char_budget:
                    break
                files.append(item)
                chars += extra

            hidden_by_budget = len(bounded_candidates) - len(files)
            more_visible = len(visible) > limit
            path_scoped = security is not None and security.is_path_scoped
            scan_saturated = len(scanned) >= scan_limit
            # Do not expose whether a scoped directory contains many entries
            # outside the principal's allowed paths.
            truncated = hidden_by_budget > 0 or more_visible or (scan_saturated and not path_scoped)

            output = "\n".join(files)
            if truncated:
                suffix = "[KITT listing bounded; narrow path or increase limit/max_tokens]"
                output = f"{output}\n{suffix}" if output else suffix

            return ToolResult(
                True,
                output,
                bytes_count=len(output.encode("utf-8")),
                truncated=truncated,
                metadata={
                    "method": "workspace_fs",
                    "estimated_tokens": (len(output.encode("utf-8")) + 3) // 4,
                    "visible_returned": len(files),
                    "scope_bounded": path_scoped,
                    "scan_saturated": scan_saturated if not path_scoped else None,
                },
            )
        except Exception as exc:
            return ToolResult(False, "", f"Directory access denied: {exc}")


class ReadFileHandler:
    def execute(self, args: Dict[str, Any], ctx: ToolContext):
        from kitt.tools.registry import ToolResult

        rel = str(args.get("path", "") or "")
        around_symbol = str(args.get("around_symbol", "") or "")
        if around_symbol and ctx.registry.repository_index is not None:
            symbol = ctx.registry.repository_index.find_symbol_location(
                around_symbol, rel or None
            )
            if not symbol:
                return ToolResult(False, "", f"Symbol not found: {around_symbol}")
            rel = symbol["path"]
            try:
                context_lines = max(0, min(int(args.get("context_lines", 20)), 200))
            except (TypeError, ValueError):
                context_lines = 20
            args["start_line"] = max(1, int(symbol["start_line"]) - context_lines)
            args["end_line"] = int(symbol["end_line"]) + context_lines

        max_tokens = _token_budget(args, 1200)
        try:
            requested_max = int(args.get("max_bytes", 0) or 0)
        except (TypeError, ValueError):
            requested_max = 0
        requested_max = max(0, min(requested_max, DEFAULT_MAX_FILE_BYTES))

        try:
            start_value = args.get("start_line")
            start_line = max(1, int(start_value if start_value is not None else 1))
            end_value = args.get("end_line")
            end_line = int(end_value) if end_value is not None else None
        except (TypeError, ValueError):
            return ToolResult(False, "", "Invalid start_line/end_line")
        if end_line is not None and end_line < start_line:
            return ToolResult(False, "", "end_line must be >= start_line")

        try:
            fs = _fs(ctx)
            relative = _scope(ctx, fs.relative(rel))
            # Keep WorkspaceFileSystem as the single filesystem trust boundary.
            # Read at the repository file-size limit; max_bytes below is an
            # output cap, matching the model-facing schema.
            data = fs.read(relative, max_bytes=DEFAULT_MAX_FILE_BYTES)
        except Exception as exc:
            return ToolResult(False, "", f"File access denied: {exc}")

        start = min(start_line - 1, max(0, data.content.count(b"\n") + 1))
        requested_end = end_line if end_line is not None else start + 200
        end = min(requested_end, start + 5000)
        text = data.content.decode("utf-8", errors="ignore")
        lines = text.splitlines()
        start = min(start, len(lines))
        end = min(end, len(lines))
        selected = lines[start:end]

        byte_budget = max_tokens * 4
        if requested_max > 0:
            byte_budget = min(byte_budget, requested_max)

        bounded: list[str] = []
        bytes_used = 0
        partial_line = False
        for line in selected:
            encoded = line.encode("utf-8")
            separator = 1 if bounded else 0
            if bounded and bytes_used + separator + len(encoded) > byte_budget:
                break
            if not bounded and len(encoded) > byte_budget:
                # Avoid returning invalid UTF-8 while making the omission explicit.
                keep = min(len(encoded), byte_budget)
                while keep > 0:
                    try:
                        prefix = encoded[:keep].decode("utf-8")
                        break
                    except UnicodeDecodeError:
                        keep -= 1
                else:
                    prefix = ""
                bounded.append(prefix)
                partial_line = True
                break
            bounded.append(line)
            bytes_used += separator + len(encoded)

        chunk = "\n".join(bounded)
        returned_end = start + len(bounded)
        selected_not_fully_returned = returned_end < end
        range_has_more = end < len(lines)
        truncated = partial_line or selected_not_fully_returned or range_has_more
        next_start_line = returned_end + 1 if truncated and not partial_line else None

        return ToolResult(
            True,
            chunk,
            bytes_count=len(chunk.encode("utf-8")),
            truncated=truncated,
            metadata={
                "method": "workspace_fs",
                "content_hash": hashlib.sha256(chunk.encode("utf-8")).hexdigest(),
                "hash_scope": "returned_range",
                "path": relative,
                "start_line": start + 1,
                "end_line": returned_end,
                "file_size": data.size,
                "mtime_ns": data.mtime_ns,
                "full_file_hash": data.sha256,
                "total_lines": len(lines),
                "omitted_lines": max(0, len(lines) - returned_end),
                "next_start_line": next_start_line,
                "partial_line_truncated": partial_line,
                "estimated_tokens": (len(chunk.encode("utf-8")) + 3) // 4,
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
                    relative,
                    content,
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
                        fs.atomic_write(relative, before_content or "", expected_exists=True, expected_sha256=digest)
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
