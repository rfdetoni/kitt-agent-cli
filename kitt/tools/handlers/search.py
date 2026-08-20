"""Search and repository map tool handlers."""
from __future__ import annotations

import re
from typing import Any, Dict

from kitt.index.scanner import RepositoryScanner
from kitt.tools.handlers import ToolContext


def _path_allowed(ctx: ToolContext, path: str) -> bool:
    security = ctx.security_context
    return security is None or security.allows_path(path)


def _filter_repository_map_rows(ctx: ToolContext, mode: str, rows: list[dict]) -> list[dict]:
    security = ctx.security_context
    if security is None or not security.is_path_scoped:
        return rows

    if mode == "workspace":
        # Workspace summaries contain aggregate file counts outside the scoped
        # principal's boundary. Returning them would leak repository structure.
        return []
    if mode in {"module", "symbol"}:
        return [row for row in rows if _path_allowed(ctx, str(row.get("path", "")))]
    if mode == "impact":
        return [
            row
            for row in rows
            if _path_allowed(ctx, str(row.get("source", "")))
            and _path_allowed(ctx, str(row.get("target", "")))
        ]
    return []


class SearchHandler:
    def execute(self, args: Dict[str, Any], ctx: ToolContext):
        from kitt.tools.registry import ToolResult

        pattern = str(args.get("pattern", ""))
        if not pattern or len(pattern) > 500:
            return ToolResult(False, "", "Invalid search pattern.")

        native_engine = getattr(ctx.registry, "native_engine", None)
        if native_engine is not None and not (ctx.security_context is not None and ctx.security_context.is_path_scoped):
            try:
                result = native_engine.search(
                    pattern,
                    regex=bool(args.get("regex", False)),
                    case_sensitive=bool(args.get("case_sensitive", False)),
                    max_results=min(int(args.get("limit", 80) or 80), 500),
                    max_per_file=min(int(args.get("max_per_file", 8) or 8), 100),
                    context_lines=min(int(args.get("context_lines", 0) or 0), 8),
                    token_budget=min(int(args.get("max_tokens", 1200) or 1200), 8000),
                )
                lines = [f"{hit['path']}:{hit['line']}:{hit['text']}" for hit in result.get("hits", [])]
                return ToolResult(
                    True, "\n".join(lines),
                    truncated=bool(result.get("omitted_matches")),
                    metadata={
                        "method": "native",
                        "backend": native_engine.status.backend,
                        "omitted_matches": result.get("omitted_matches", 0),
                        "estimated_tokens": result.get("estimated_tokens", 0),
                    },
                )
            except Exception:
                # Native optimization is never allowed to make search unavailable.
                pass

        if not bool(args.get("regex", False)) and ctx.registry.repository_index is not None:
            ctx.registry._refresh_index()
            rows = ctx.registry.repository_index.search_text(pattern, limit=200)
            terms = ctx.registry.repository_index._query_terms(pattern)
            matches: list[str] = []
            for row in rows:
                path = str(row.get("path") or "")
                if not path or not _path_allowed(ctx, path):
                    continue
                content = row.get("content", "")
                for line_number, line in enumerate(content.splitlines(), 1):
                    if any(term.lower() in line.lower() for term in terms):
                        matches.append(f"{path}:{line_number}:{line[:300]}")
                        break
                if len(matches) >= 200:
                    break
            return ToolResult(
                True,
                "\n".join(matches),
                truncated=len(matches) >= 200,
                metadata={"method": "index"},
            )

        try:
            expression = re.compile(pattern)
        except re.error as exc:
            return ToolResult(False, "", f"Invalid regex: {exc}")

        matches: list[str] = []
        for path in RepositoryScanner(ctx.registry.root_path).scan_files():
            if len(matches) >= 200:
                break
            try:
                relative = str(path.relative_to(ctx.registry.root_path))
                if not _path_allowed(ctx, relative):
                    continue
                with path.open("r", encoding="utf-8", errors="ignore") as handle:
                    for line_number, line in enumerate(handle, 1):
                        if line_number > 5000:
                            break
                        if expression.search(line):
                            matches.append(
                                f"{relative}:{line_number}:{line.rstrip()[:300]}"
                            )
                            if len(matches) >= 200:
                                break
            except OSError:
                continue
        return ToolResult(True, "\n".join(matches), truncated=len(matches) >= 200)


class RepositoryMapHandler:
    def execute(self, args: Dict[str, Any], ctx: ToolContext):
        from kitt.tools.registry import ToolResult

        if ctx.registry.repository_index is None:
            return ToolResult(False, "", "Repository index unavailable.")

        ctx.registry._refresh_index()
        mode = str(args.get("mode", "workspace") or "workspace")
        if ctx.security_context is not None and ctx.security_context.is_path_scoped and mode == "workspace":
            return ToolResult(
                False,
                "",
                "Workspace-wide repository map is unavailable to a path-scoped principal.",
            )

        rows = ctx.registry.repository_index.repository_map(
            mode=mode,
            query=str(args.get("query", "") or ""),
            path=str(args.get("path", "") or ""),
            limit=min(int(args.get("limit", 80)), 500),
        )
        rows = _filter_repository_map_rows(ctx, mode, rows)
        output = ctx.registry._format_repository_map(mode, rows)
        max_tokens = min(int(args.get("max_tokens", 1200)), 4000)
        max_chars = max_tokens * 4
        return ToolResult(
            True,
            output[:max_chars],
            truncated=len(output) > max_chars,
            metadata={"method": "index", "mode": mode, "rows": len(rows)},
        )
