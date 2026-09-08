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
    return max(64, min(int(args.get("max_tokens", default) or default), 32_000))


class ListFilesHandler:
    def execute(self, args: Dict[str, Any], ctx: ToolContext):
        from kitt.tools.registry import ToolResult

        rel = str(args.get("path", ".") or ".")
        limit = max(1, min(int(args.get("limit", 100) or 100), 500))
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

            native = getattr(ctx.registry, "native_engine", None)
            if (
                native is not None
                and getattr(getattr(native, "status", None), "available", False)
                and not (security is not None and security.is_path_scoped)
            ):
                try:
                    data = native.list_files(rel, limit=limit, token_budget=max_tokens)
                    output = "\n".join(str(item) for item in data.get("files", []))
                    omitted = int(data.get("omitted", 0) or 0)
                    if omitted:
                        suffix = (
                            f"\n[KITT {omitted} file(s) omitted; narrow path "
                            "or increase limit/max_tokens]"
                        )
                        output += suffix
                    return ToolResult(
                        True,
                        output,
                        bytes_count=len(output.encode("utf-8")),
                        truncated=omitted > 0,
                        metadata={
                            "method": "native",
                            "backend": getattr(native.status, "backend", "rust"),
                            "omitted": omitted,
                            "estimated_tokens": int(
                                data.get("estimated_tokens", 0) or 0
                            ),
                        },
                    )
                except Exception:
                    # Native optimization may not make repository access unavailable.
                    pass

            files = []
            char_budget = max_tokens * 4
            chars = 0
            scanned = fs.list_regular_files(rel, limit=limit)
            for item in scanned:
                try:
                    item = _scope(ctx, item)
                except PermissionError:
                    continue
                extra = len(item) + (1 if files else 0)
                if files and chars + extra > char_budget:
                    break
                files.append(item)
                chars += extra
            omitted = max(0, len(scanned) - len(files))
            output = "\n".join(files)
            if omitted:
                output += f"\n[KITT {omitted} file(s) omitted; increase max_tokens]"
            return ToolResult(
                True,
                output,
                bytes_count=len(output.encode("utf-8")),
                truncated=omitted > 0,
                metadata={
                    "method": "python",
                    "omitted": omitted,
                    "estimated_tokens": (len(output.encode("utf-8")) + 3) // 4,
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
            context_lines = max(0, min(int(args.get("context_lines", 20)), 200))
            args["start_line"] = max(1, int(symbol["start_line"]) - context_lines)
            args["end_line"] = int(symbol["end_line"]) + context_lines

        max_tokens = _token_budget(args, 1200)
        requested_max = int(args.get("max_bytes", 0) or 0)
        max_bytes = (
            min(requested_max, DEFAULT_MAX_FILE_BYTES)
            if requested_max > 0
            else DEFAULT_MAX_FILE_BYTES
        )
        start_value = args.get("start_line")
        start_line = max(1, int(start_value if start_value is not None else 1))
        end_value = args.get("end_line")
        end_line = int(end_value) if end_value is not None else None

        try:
            fs = _fs(ctx)
            relative = _scope(ctx, fs.relative(rel))
        except Exception as exc:
            return ToolResult(False, "", f"File access denied: {exc}")

        native = getattr(ctx.registry, "native_engine", None)
        if (
            native is not None
            and getattr(getattr(native, "status", None), "available", False)
        ):
            try:
                data = native.read_file(
                    relative,
                    start_line=start_line,
                    end_line=end_line,
                    max_bytes=max_bytes,
                    token_budget=max_tokens,
                )
                chunk = str(data.get("content", ""))
                next_start = data.get("next_start_line")
                return ToolResult(
                    True,
                    chunk,
                    bytes_count=len(chunk.encode("utf-8")),
                    truncated=next_start is not None,
                    metadata={
                        "method": "native",
                        "backend": getattr(native.status, "backend", "rust"),
                        "content_hash": str(data.get("content_hash", "")),
                        "hash_scope": "returned_range",
                        "path": str(data.get("path", relative)),
                        "start_line": int(data.get("start_line", start_line)),
                        "end_line": int(data.get("end_line", start_line)),
                        "file_size": int(data.get("file_size", 0)),
                        "mtime_ns": int(data.get("mtime_ns", 0)),
                        "full_file_hash": str(data.get("full_file_hash", "")),
                        "total_lines": int(data.get("total_lines", 0)),
                        "omitted_lines": int(data.get("omitted_lines", 0)),
                        "next_start_line": next_start,
                        "estimated_tokens": int(
                            data.get("estimated_tokens", 0) or 0
                        ),
                    },
                )
            except Exception:
                # Preserve the existing safe Python path as compatibility fallback.
                pass

        try:
            data = fs.read(relative, max_bytes=DEFAULT_MAX_FILE_BYTES)
        except Exception as exc:
            return ToolResult(False, "", f"File access denied: {exc}")

        start = start_line - 1
        requested_end = end_line if end_line is not None else start + 200
        end = min(requested_end, start + 5000)
        text = data.content.decode("utf-8", errors="ignore")
        lines = text.splitlines()
        selected = lines[start:end]

        char_budget = max_tokens * 4
        bounded = []
        chars = 0
        for line in selected:
            extra = len(line) + (1 if bounded else 0)
            if bounded and chars + extra > char_budget:
                break
            if not bounded and extra > char_budget:
                bounded.append(line[:char_budget])
                break
            bounded.append(line)
            chars += extra
        chunk = "\n".join(bounded)
        returned_end = start + len(bounded)
        truncated = returned_end < min(end, len(lines)) or end < len(lines)
        next_start_line = returned_end + 1 if truncated else None

        if requested_max > 0:
            encoded = chunk.encode("utf-8")
            if len(encoded) > requested_max:
                encoded = encoded[:requested_max]
                chunk = encoded.decode("utf-8", errors="ignore")
                truncated = True

        return ToolResult(
            True,
            chunk,
            bytes_count=len(chunk.encode("utf-8")),
            truncated=truncated,
            metadata={
                "method": "python",
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
            return ToolResult(
                False,
                "",
                f"write_file content exceeds {DEFAULT_MAX_FILE_BYTES} bytes",
            )

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
                        fs.atomic_write(
                            relative,
                            before_content or "",
                            expected_exists=True,
                            expected_sha256=digest,
                        )
                    else:
                        fs.unlink(
                            relative,
                            expected_exists=True,
                            expected_sha256=digest,
                        )
                    raise
        except Exception as exc:
            return ToolResult(False, "", f"Workspace write refused: {exc}")

        ctx.registry._refresh_index([relative])
        return ToolResult(
            True,
            f"Successfully wrote {len(content.encode('utf-8'))} bytes to {relative}.",
            metadata={
                "content_hash": digest,
                "path": relative,
                "changeset": changeset,
            },
        )
