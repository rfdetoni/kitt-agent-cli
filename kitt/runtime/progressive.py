"""LLM-oriented progressive disclosure helpers for repository retrieval."""
from __future__ import annotations

import json
from typing import Any


def _tokens(value: Any) -> int:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")
    ).encode("utf-8")
    return (len(raw) + 3) // 4


def _hits_from_data(data: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if isinstance(data, dict):
        hits = data.get("hits")
        if isinstance(hits, list):
            return [item for item in hits if isinstance(item, dict)], dict(data)
    if isinstance(data, str):
        hits: list[dict[str, Any]] = []
        for line in data.splitlines():
            parts = line.split(":", 2)
            if len(parts) < 2:
                continue
            try:
                number = int(parts[1])
            except ValueError:
                continue
            hits.append(
                {
                    "path": parts[0],
                    "line": number,
                    "text": parts[2] if len(parts) > 2 else "",
                    "score": 0.0,
                }
            )
        return hits, {"hits": hits}
    return [], {}


def apply_progressive_search_view(result, arguments: dict[str, Any]):
    """Default SafeRuntime search to file-level discovery.

    `view=matches` keeps the original detailed payload. Direct legacy `search`
    remains unchanged; this only affects the compact SafeRuntime surface.
    """
    if not getattr(result, "success", False):
        return result
    view = str(arguments.get("view", "files") or "files").strip().lower()
    if view in {"matches", "detail", "detailed"}:
        return result
    if view not in {"files", "summary"}:
        result.success = False
        result.error = "repo.search view must be 'files' or 'matches'"
        return result

    hits, original = _hits_from_data(getattr(result, "data", None))
    if not hits:
        return result

    try:
        max_files = max(1, min(int(arguments.get("max_files", 24) or 24), 100))
    except (TypeError, ValueError):
        max_files = 24

    grouped: dict[str, dict[str, Any]] = {}
    for hit in hits:
        path = str(hit.get("path") or "")
        if not path:
            continue
        row = grouped.setdefault(
            path,
            {
                "path": path,
                "matches": 0,
                "best_score": hit.get("score", 0.0),
                "first_line": hit.get("line"),
            },
        )
        row["matches"] += 1
        score = hit.get("score")
        try:
            if score is not None and float(score) > float(row["best_score"] or 0):
                row["best_score"] = score
        except (TypeError, ValueError):
            pass

    files = sorted(
        grouped.values(),
        key=lambda row: (-int(row["matches"]), str(row["path"]).casefold()),
    )
    omitted_files = max(0, len(files) - max_files)
    files = files[:max_files]

    compact = {
        "query": str(arguments.get("query", arguments.get("pattern", ""))),
        "view": "files",
        "files": files,
        "matched_files": len(grouped),
        "omitted_files": omitted_files,
        "omitted_matches": int(original.get("omitted_matches", 0) or 0),
        "hint": (
            "Read the highest-ranked file with repo.read, or repeat repo.search "
            "with view='matches' only when exact match lines are required."
        ),
    }
    snippet_bytes = sum(
        len(str(hit.get("text") or "").encode("utf-8"))
        + sum(len(str(x).encode("utf-8")) for x in hit.get("before", []))
        + sum(len(str(x).encode("utf-8")) for x in hit.get("after", []))
        for hit in hits
    )
    snippet_tokens = (snippet_bytes + 3) // 4 if snippet_bytes else 0
    before = _tokens(getattr(result, "data", None))
    after = _tokens(compact)
    saved = max(before - after, snippet_tokens)
    if saved <= 0 and len(hits) > len(files):
        saved = max(1, len(hits) - len(files))
    saved = max(0, saved)
    result.data = compact
    result.tokens_saved = int(getattr(result, "tokens_saved", 0) or 0) + saved
    metadata = dict(getattr(result, "metadata", {}) or {})
    metadata.update(
        {
            "aci_progressive": True,
            "search_view": "files",
            "raw_estimated_tokens": max(
                int(metadata.get("raw_estimated_tokens", 0) or 0), before
            ),
            "output_estimated_tokens": after,
            "tokens_saved": int(metadata.get("tokens_saved", 0) or 0) + saved,
            "context_avoidance_tokens": int(
                metadata.get("context_avoidance_tokens", 0) or 0
            ) + saved,
        }
    )
    result.metadata = metadata
    return result
