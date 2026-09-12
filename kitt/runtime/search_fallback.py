"""Complete, bounded filesystem fallback for SafeRuntime repository search."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from kitt.index.scanner import RepositoryScanner


def full_scan_search(
    root: str | Path,
    args: dict[str, Any],
    *,
    path_allowed=None,
) -> dict[str, Any]:
    pattern = str(args.get("query", args.get("pattern", "")) or "")
    if not pattern or len(pattern) > 500:
        raise ValueError("Invalid search pattern")
    regex_mode = bool(args.get("regex", False))
    case_sensitive = bool(args.get("case_sensitive", False))
    try:
        expression = re.compile(
            pattern if regex_mode else re.escape(pattern),
            0 if case_sensitive else re.IGNORECASE,
        )
    except re.error as exc:
        raise ValueError(f"Invalid regex: {exc}") from exc

    def bounded_int(value, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(parsed, maximum))

    max_results = bounded_int(args.get("max_results", args.get("limit", 80)), 80, 1, 500)
    max_per_file = bounded_int(args.get("max_per_file", 8), 8, 1, 100)
    max_tokens = bounded_int(args.get("max_tokens", args.get("token_budget", 1200)), 1200, 64, 8000)
    byte_budget = max_tokens * 4
    root_path = Path(root).resolve()
    allowed = path_allowed or (lambda _path: True)

    hits: list[dict[str, Any]] = []
    per_file: dict[str, int] = {}
    matched_files: set[str] = set()
    total_seen = 0
    bytes_used = 0
    budget_exhausted = False
    result_limit_hit = False

    for path in RepositoryScanner(root_path).scan_files():
        try:
            relative = path.relative_to(root_path).as_posix()
        except ValueError:
            continue
        if not allowed(relative):
            continue
        try:
            if path.is_symlink() or not path.is_file():
                continue
            with path.open("r", encoding="utf-8", errors="ignore") as handle:
                for line_number, line in enumerate(handle, 1):
                    match = expression.search(line)
                    if match is None:
                        continue
                    total_seen += 1
                    matched_files.add(relative)
                    used = per_file.get(relative, 0)
                    if used >= max_per_file:
                        continue
                    if len(hits) >= max_results:
                        result_limit_hit = True
                        break
                    rendered = line.rstrip("\r\n")
                    if len(rendered) > 300:
                        start = max(0, match.start() - 120)
                        end = min(len(rendered), match.end() + 160)
                        rendered = ("…" if start else "") + rendered[start:end] + ("…" if end < len(line.rstrip()) else "")
                    hit = {
                        "path": relative,
                        "line": line_number,
                        "column": match.start() + 1,
                        "text": rendered,
                        "before": [],
                        "after": [],
                        "score": 0.0,
                    }
                    cost = len(relative.encode("utf-8")) + len(rendered.encode("utf-8")) + 48
                    if hits and bytes_used + cost > byte_budget:
                        budget_exhausted = True
                        break
                    hits.append(hit)
                    per_file[relative] = used + 1
                    bytes_used += cost
                if budget_exhausted or result_limit_hit:
                    break
        except (OSError, UnicodeError):
            continue

    omitted = max(0, total_seen - len(hits))
    if budget_exhausted or result_limit_hit:
        omitted = max(1, omitted)
    return {
        "hits": hits,
        "matched_files": len(matched_files),
        "total_matches_seen": total_seen,
        "omitted_matches": omitted,
        "estimated_tokens": (bytes_used + 3) // 4,
        "backend": "scanner",
        "complete_file_scan": True,
        "budget_exhausted": budget_exhausted,
        "result_limit_hit": result_limit_hit,
    }
