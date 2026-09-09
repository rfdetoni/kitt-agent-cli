"""Search and repository map tool handlers."""
from __future__ import annotations

import re
from typing import Any, Callable, Dict

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


def _safe_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _estimated_tokens(text: str) -> int:
    return (len(text.encode("utf-8")) + 3) // 4


def _truncate_utf8(text: str, max_bytes: int) -> str:
    payload = text.encode("utf-8")
    if len(payload) <= max_bytes:
        return text
    keep = max(0, max_bytes)
    while keep > 0:
        try:
            return payload[:keep].decode("utf-8")
        except UnicodeDecodeError:
            keep -= 1
    return ""


def _compact_match_line(line: str, char_column: int, needle_chars: int) -> str:
    if len(line.encode("utf-8")) <= 360:
        return line
    left = max(0, char_column - 140)
    right = min(len(line), char_column + max(1, needle_chars) + 140)
    return (
        ("…" if left else "")
        + line[left:right]
        + ("…" if right < len(line) else "")
    )


def indexed_literal_search(
    index,
    pattern: str,
    *,
    path_allowed: Callable[[str], bool] | None = None,
    case_sensitive: bool = False,
    max_results: int = 80,
    max_per_file: int = 8,
    token_budget: int = 1200,
) -> dict[str, Any] | None:
    """Use the persistent RepositoryIndex as the first literal-search path.

    Returns ``None`` only when the index is partial/degraded and provides no
    exact hit, signalling that the caller should fall back to a filesystem
    scan.  A READY index is authoritative for literal search.
    """
    if not pattern:
        return {
            "hits": [],
            "matched_files": 0,
            "total_matches_seen": 0,
            "omitted_matches": 0,
            "estimated_tokens": 0,
            "backend": "index",
            "index_state": "UNKNOWN",
        }

    max_results = _safe_int(max_results, 80, 1, 500)
    max_per_file = _safe_int(max_per_file, 8, 1, 100)
    token_budget = _safe_int(token_budget, 1200, 64, 8000)
    candidate_limit = min(500, max(80, max_results * 4))

    try:
        state = str(index.metadata().get("state", "UNKNOWN"))
    except Exception:
        state = "UNKNOWN"

    rows = index.search_text(pattern, limit=candidate_limit)
    allow = path_allowed or (lambda _path: True)
    needle = pattern if case_sensitive else pattern.casefold()

    hits: list[dict[str, Any]] = []
    matched_files: set[str] = set()
    per_file: dict[str, int] = {}
    exact_seen = 0
    tokens = 0
    budget_exhausted = False
    result_limit_hit = False
    seen_locations: set[tuple[str, int, str]] = set()

    for row in rows:
        path = str(row.get("path") or "")
        if not path or not allow(path):
            continue
        content = str(row.get("content") or "")
        try:
            chunk_start = max(1, int(row.get("start_line", 1) or 1))
        except (TypeError, ValueError):
            chunk_start = 1

        for offset, line in enumerate(content.splitlines()):
            haystack = line if case_sensitive else line.casefold()
            char_column = haystack.find(needle)
            match_len = len(pattern)
            if char_column < 0:
                query_terms_fn = getattr(index, "_query_terms", None)
                if query_terms_fn is not None:
                    candidates = query_terms_fn(pattern)
                else:
                    words = re.findall(r"[A-Za-z0-9_.$#]{2,}", pattern)
                    candidates = ["_".join(words)] if len(words) > 1 else []
                for term in sorted(candidates, key=len, reverse=True):
                    t_needle = term if case_sensitive else term.casefold()
                    col = haystack.find(t_needle)
                    if col >= 0:
                        char_column = col
                        match_len = len(term)
                        break
            if char_column < 0:
                continue

            line_number = chunk_start + offset
            location = (path, line_number, line)
            if location in seen_locations:
                continue
            seen_locations.add(location)
            exact_seen += 1

            used = per_file.get(path, 0)
            if used >= max_per_file:
                continue
            if len(hits) >= max_results:
                result_limit_hit = True
                break

            rendered = _compact_match_line(line, char_column, match_len)
            hit_tokens = _estimated_tokens(path) + _estimated_tokens(rendered) + 12
            if hits and tokens + hit_tokens > token_budget:
                budget_exhausted = True
                break
            if not hits and hit_tokens > token_budget:
                available = max(32, token_budget * 4 - len(path.encode("utf-8")) - 48)
                rendered = _truncate_utf8(rendered, available)
                hit_tokens = min(token_budget, _estimated_tokens(path) + _estimated_tokens(rendered) + 12)

            tokens += hit_tokens
            per_file[path] = used + 1
            matched_files.add(path)
            hits.append(
                {
                    "path": path,
                    "line": line_number,
                    "column": char_column + 1,
                    "text": rendered,
                    "before": [],
                    "after": [],
                    "score": row.get("score", 0.0),
                }
            )

        if budget_exhausted or result_limit_hit:
            break

    candidate_saturated = len(rows) >= candidate_limit
    if not hits and (state != "READY" or candidate_saturated):
        # A partial/degraded index, or a saturated candidate window, cannot
        # prove absence of an exact literal match. Let the caller fall back to
        # the bounded filesystem/native search instead of returning a false negative.
        return None

    omitted = max(0, exact_seen - len(hits))
    if candidate_saturated or budget_exhausted or result_limit_hit:
        omitted = max(1, omitted)

    return {
        "hits": hits,
        "matched_files": len(matched_files),
        "total_matches_seen": exact_seen,
        "omitted_matches": omitted,
        "estimated_tokens": tokens,
        "backend": "index",
        "index_state": state,
        "candidate_rows": len(rows),
        "candidate_saturated": candidate_saturated,
        "budget_exhausted": budget_exhausted,
    }


class SearchHandler:
    def execute(self, args: Dict[str, Any], ctx: ToolContext):
        from kitt.tools.registry import ToolResult

        pattern = str(args.get("pattern", args.get("query", "")) or "")
        if not pattern or len(pattern) > 500:
            return ToolResult(False, "", "Invalid search pattern.")

        regex_mode = bool(args.get("regex", False))
        case_sensitive = bool(args.get("case_sensitive", False))
        limit = _safe_int(args.get("limit", args.get("max_results", 80)), 80, 1, 500)
        max_per_file = _safe_int(args.get("max_per_file", 8), 8, 1, 100)
        max_tokens = _safe_int(
            args.get("max_tokens", args.get("token_budget", 1200)), 1200, 64, 8000
        )

        index = ctx.registry.repository_index
        if not regex_mode and index is not None:
            ctx.registry._refresh_index()
            indexed = indexed_literal_search(
                index,
                pattern,
                path_allowed=lambda path: _path_allowed(ctx, path),
                case_sensitive=case_sensitive,
                max_results=limit,
                max_per_file=max_per_file,
                token_budget=max_tokens,
            )
            if indexed is not None:
                lines = [
                    f"{hit['path']}:{hit['line']}:{hit['text']}"
                    for hit in indexed.get("hits", [])
                ]
                return ToolResult(
                    True,
                    "\n".join(lines),
                    truncated=bool(indexed.get("omitted_matches")),
                    metadata={
                        "method": "index",
                        "backend": "sqlite_fts",
                        "omitted_matches": indexed.get("omitted_matches", 0),
                        "estimated_tokens": indexed.get("estimated_tokens", 0),
                        "index_state": indexed.get("index_state", "UNKNOWN"),
                        "candidate_rows": indexed.get("candidate_rows", 0),
                        "candidate_saturated": indexed.get("candidate_saturated", False),
                    },
                )

        native_engine = getattr(ctx.registry, "native_engine", None)
        if (
            native_engine is not None
            and not (ctx.security_context is not None and ctx.security_context.is_path_scoped)
        ):
            try:
                result = native_engine.search(
                    pattern,
                    regex=regex_mode,
                    case_sensitive=case_sensitive,
                    max_results=limit,
                    max_per_file=max_per_file,
                    context_lines=_safe_int(args.get("context_lines", 0), 0, 0, 8),
                    token_budget=max_tokens,
                )
                lines = [
                    f"{hit['path']}:{hit['line']}:{hit['text']}"
                    for hit in result.get("hits", [])
                ]
                return ToolResult(
                    True,
                    "\n".join(lines),
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

        if not regex_mode:
            # A partial/degraded index with no exact hit and no native engine
            # gets a bounded safe scanner fallback.
            expression = re.compile(re.escape(pattern), 0 if case_sensitive else re.IGNORECASE)
        else:
            try:
                expression = re.compile(pattern, 0 if case_sensitive else re.IGNORECASE)
            except re.error as exc:
                return ToolResult(False, "", f"Invalid regex: {exc}")

        matches: list[str] = []
        bytes_used = 0
        byte_budget = max_tokens * 4
        for path in RepositoryScanner(ctx.registry.root_path).scan_files():
            if len(matches) >= limit:
                break
            try:
                relative = path.relative_to(ctx.registry.root_path).as_posix()
                if not _path_allowed(ctx, relative):
                    continue
                with path.open("r", encoding="utf-8", errors="ignore") as handle:
                    for line_number, line in enumerate(handle, 1):
                        if line_number > 5000:
                            break
                        if expression.search(line):
                            rendered = f"{relative}:{line_number}:{line.rstrip()[:300]}"
                            extra = len(rendered.encode("utf-8")) + (1 if matches else 0)
                            if matches and bytes_used + extra > byte_budget:
                                return ToolResult(
                                    True,
                                    "\n".join(matches),
                                    truncated=True,
                                    metadata={"method": "scanner", "budget_exhausted": True},
                                )
                            matches.append(rendered)
                            bytes_used += extra
                            if len(matches) >= limit:
                                break
            except OSError:
                continue
        return ToolResult(
            True,
            "\n".join(matches),
            truncated=len(matches) >= limit,
            metadata={"method": "scanner"},
        )


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
            limit=_safe_int(args.get("limit", 80), 80, 1, 500),
        )
        rows = _filter_repository_map_rows(ctx, mode, rows)
        output = ctx.registry._format_repository_map(mode, rows)
        max_tokens = _safe_int(args.get("max_tokens", 1200), 1200, 64, 4000)
        max_bytes = max_tokens * 4
        encoded = output.encode("utf-8")
        if len(encoded) > max_bytes:
            output = _truncate_utf8(output, max_bytes)
        return ToolResult(
            True,
            output,
            truncated=len(encoded) > max_bytes,
            metadata={"method": "index", "mode": mode, "rows": len(rows)},
        )
