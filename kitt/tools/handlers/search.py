"""Search and repository map tool handlers."""
from __future__ import annotations

import re
from typing import Any, Dict, List
from kitt.tools.handlers import ToolContext
from kitt.index.scanner import RepositoryScanner


class SearchHandler:
    def execute(self, args: Dict[str, Any], ctx: ToolContext):
        from kitt.tools.registry import ToolResult
        pattern = str(args.get("pattern", ""))
        if not pattern or len(pattern) > 500:
            return ToolResult(False, "", "Invalid search pattern.")
        if not bool(args.get("regex", False)) and ctx.registry.repository_index is not None:
            ctx.registry._refresh_index()
            rows = ctx.registry.repository_index.search_text(pattern, limit=200)
            terms = ctx.registry.repository_index._query_terms(pattern)
            matches = []
            for row in rows:
                path = row.get("path")
                content = row.get("content", "")
                if not path:
                    continue
                for no, line in enumerate(content.splitlines(), 1):
                    if any(term.lower() in line.lower() for term in terms):
                        matches.append(f"{path}:{no}:{line[:300]}")
                        break
                if len(matches) >= 200:
                    break
            return ToolResult(True, "\n".join(matches), truncated=len(matches) >= 200, metadata={"method": "index"})
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            return ToolResult(False, "", f"Invalid regex: {exc}")
        matches = []
        for path in RepositoryScanner(ctx.registry.root_path).scan_files():
            if len(matches) >= 200:
                break
            try:
                rel_path = path.relative_to(ctx.registry.root_path)
                with path.open("r", encoding="utf-8", errors="ignore") as fh:
                    for no, line in enumerate(fh, 1):
                        if no > 5000:
                            break
                        if rx.search(line):
                            matches.append(f"{rel_path}:{no}:{line.rstrip()[:300]}")
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
        rows = ctx.registry.repository_index.repository_map(
            mode=mode,
            query=str(args.get("query", "") or ""),
            path=str(args.get("path", "") or ""),
            limit=min(int(args.get("limit", 80)), 500),
        )
        output = ctx.registry._format_repository_map(mode, rows)
        max_tokens = min(int(args.get("max_tokens", 1200)), 4000)
        max_chars = max_tokens * 4
        return ToolResult(
            True,
            output[:max_chars],
            truncated=len(output) > max_chars,
            metadata={"method": "index", "mode": mode, "rows": len(rows)},
        )
