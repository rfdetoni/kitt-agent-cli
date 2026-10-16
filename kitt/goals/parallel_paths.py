"""Conservative path overlap checks for parallel Goal tasks.

These checks schedule work, not authorize filesystem access. Workspace path
security remains the responsibility of the tool registry.
"""
from __future__ import annotations

import posixpath
from collections.abc import Iterable


def _scope(path: str) -> str:
    if not isinstance(path, str) or not path.strip():
        return "."
    raw = path.strip().replace("\\", "/")
    # Unknown, absolute and glob scopes cannot safely run in parallel.
    if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
        return "."
    if any(marker in raw for marker in "*?["):
        return "."
    normalized = posixpath.normpath(raw)
    if normalized == ".." or normalized.startswith("../"):
        return "."
    return normalized


def paths_overlap(first: Iterable[str], second: Iterable[str]) -> bool:
    """Treat directory ancestors and ambiguous scopes as shared resources."""
    left = [_scope(path) for path in first]
    right = [_scope(path) for path in second]
    if not left or not right:
        return True
    for a in left:
        for b in right:
            if a == "." or b == "." or a == b:
                return True
            if a.startswith(b + "/") or b.startswith(a + "/"):
                return True
    return False
