"""Normalize progress evidence only; never apply these transformations to tool identity."""
from __future__ import annotations

import re


_TIMESTAMP = re.compile(
    r"\b20\d\d-\d\d-\d\d[T ]\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:?\d\d)?\b"
)


def normalize_progress_arguments(operation: str, arguments: object) -> object:
    """Normalize equivalent observational args without changing tool execution."""
    if not isinstance(arguments, dict):
        return arguments
    result = dict(arguments)
    for key in ("path", "file"):
        path = result.get(key)
        if isinstance(path, str):
            normalized = path.replace("\\", "/")
            while normalized.startswith("./"):
                normalized = normalized[2:]
            result[key] = normalized
    if operation in {"repo.search", "search"}:
        query = result.get("query")
        if isinstance(query, str):
            query = query.strip()
            # Do not reorder phrases, boolean expressions or regexes.
            if re.fullmatch(r"[\w-]+(?:\s+[\w-]+)*", query):
                result["query"] = " ".join(sorted(term.casefold() for term in query.split()))
    return result


def normalize_failure_diagnostic(value: object) -> str:
    """Discard transient timestamps only in failed progress comparisons."""
    return _TIMESTAMP.sub("<timestamp>", str(value or "").strip())
