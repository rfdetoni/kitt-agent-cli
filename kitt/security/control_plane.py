"""Authority classification for KITT control-plane workspace paths."""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Iterable


CONTROL_PLANE_SEGMENTS = frozenset({".kitt"})


def normalize_workspace_path(value: object) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    while raw.startswith("./"):
        raw = raw[2:]
    if not raw:
        return "."
    return PurePosixPath(raw).as_posix()


def is_control_plane_path(value: object) -> bool:
    normalized = normalize_workspace_path(value)
    return any(
        part.casefold() in CONTROL_PLANE_SEGMENTS
        for part in PurePosixPath(normalized).parts
    )


def control_plane_paths(values: Iterable[object]) -> tuple[str, ...]:
    paths = {
        normalize_workspace_path(value)
        for value in values
        if str(value or "").strip() and is_control_plane_path(value)
    }
    return tuple(sorted(paths))
