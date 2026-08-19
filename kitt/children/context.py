from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional


def _contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _dedupe_scopes(paths: Iterable[Path]) -> list[Path]:
    unique = sorted(set(paths), key=lambda path: (len(path.parts), str(path)))
    result: list[Path] = []
    for path in unique:
        if any(_contains(existing, path) for existing in result):
            continue
        result.append(path)
    return result


def validate_child_paths(root_dir, allowed_paths) -> list[str]:
    root = Path(root_dir).resolve()
    output: list[Path] = []
    for value in allowed_paths:
        target = (root / value).resolve(strict=False)
        if not _contains(root, target):
            raise ValueError("Child path escapes workspace")
        output.append(target)
    return [str(path.relative_to(root)) for path in _dedupe_scopes(output)]


def narrow_child_paths(
    root_dir,
    requested_paths: Iterable[str],
    parent_paths: Optional[Iterable[str]],
) -> list[str]:
    """Intersect child path requests with an optional parent path scope.

    An empty ``requested_paths`` means "inherit parent scope". If the parent is
    workspace-wide (``parent_paths is None``), an empty result represents the
    existing KITT convention of unrestricted-within-workspace.
    """

    root = Path(root_dir).resolve()
    requested = [
        (root / path).resolve(strict=False)
        for path in validate_child_paths(root, requested_paths)
    ]

    if parent_paths is None:
        reduced = _dedupe_scopes(requested)
        if any(path == root for path in reduced):
            return []
        return [str(path.relative_to(root)) for path in reduced]

    parents = [
        (root / path).resolve(strict=False)
        for path in validate_child_paths(root, parent_paths)
    ]
    if not requested:
        return [str(path.relative_to(root)) for path in _dedupe_scopes(parents)]

    intersections: list[Path] = []
    for parent in parents:
        for requested_path in requested:
            if _contains(parent, requested_path):
                intersections.append(requested_path)
            elif _contains(requested_path, parent):
                intersections.append(parent)

    intersections = _dedupe_scopes(intersections)
    if not intersections:
        raise PermissionError("Child requested paths outside parent path scope")
    return [str(path.relative_to(root)) for path in intersections]
