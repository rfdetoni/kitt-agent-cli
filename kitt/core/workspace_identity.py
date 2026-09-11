"""Immutable workspace identity shared by every KITT component.

The project historically mixed three incompatible notions of "workspace":

- the canonical path,
- a SHA-256 hash of that path,
- the persisted ``workspaces.id`` row.

Foreign keys for artifacts, children and pending actions broke whenever a
component passed a path where an id was expected.  This module defines the
single contract: ``workspace_id`` always means ``workspaces.id`` and
``canonical_root`` always means the normalized absolute path.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


def canonical_workspace_path(root_path: str | Path) -> str:
    return str(Path(root_path).expanduser().resolve(strict=False))


@dataclass(frozen=True)
class WorkspaceIdentity:
    """Immutable identity binding persisted id, canonical root and hash."""

    id: str
    canonical_root: Path
    canonical_path_hash: str

    @classmethod
    def build(cls, root_path: str | Path) -> "WorkspaceIdentity":
        from kitt.history.repository import get_or_create_workspace_identity

        return get_or_create_workspace_identity(canonical_workspace_path(root_path))

    @staticmethod
    def path_hash(root_path: str | Path) -> str:
        canon = canonical_workspace_path(root_path)
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"WorkspaceIdentity(id={self.id!r}, root={str(self.canonical_root)!r})"

    def __getitem__(self, key: str):
        """Backward-compatible dict-style access for legacy call sites."""
        if key == "id":
            return self.id
        if key == "canonical_path_hash":
            return self.canonical_path_hash
        if key == "canonical_root":
            return self.canonical_root
        if key == "git_root":
            return str(self.canonical_root)
        if key == "display_name":
            return self.canonical_root.name
        raise KeyError(key)
