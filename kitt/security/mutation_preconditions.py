"""Canonical mutation preconditions for optimistic concurrency and approval validation."""
from __future__ import annotations

import hashlib
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from kitt.security.workspace_fs import WorkspaceFileSystem


@dataclass(frozen=True)
class MutationPrecondition:
    path: str
    expected_exists: bool
    expected_sha256: Optional[str] = None
    size: Optional[int] = None
    mtime_ns: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MutationPrecondition:
        return cls(
            path=str(data.get("path", "")),
            expected_exists=bool(data.get("expected_exists", False)),
            expected_sha256=data.get("expected_sha256"),
            size=data.get("size"),
            mtime_ns=data.get("mtime_ns"),
        )


def capture_preconditions(
    root_dir: str | Path,
    tool_name: str,
    normalized_args: dict,
) -> List[MutationPrecondition]:
    """Capture explicit existence and hash preconditions for a requested tool mutation."""
    preconditions: List[MutationPrecondition] = []
    root = Path(root_dir).resolve()
    fs = WorkspaceFileSystem(root)

    if tool_name == "write_file":
        rel = str(normalized_args.get("path") or normalized_args.get("file") or "").strip()
        if rel:
            try:
                rel_path = fs.relative(rel)
                target = root / rel_path
                if target.is_file() and not target.is_symlink():
                    data = fs.read(rel_path)
                    preconditions.append(
                        MutationPrecondition(
                            path=rel_path,
                            expected_exists=True,
                            expected_sha256=data.sha256,
                            size=data.size,
                            mtime_ns=data.mtime_ns,
                        )
                    )
                else:
                    preconditions.append(
                        MutationPrecondition(
                            path=rel_path,
                            expected_exists=False,
                            expected_sha256=None,
                        )
                    )
            except Exception:
                preconditions.append(
                    MutationPrecondition(
                        path=rel,
                        expected_exists=False,
                        expected_sha256=None,
                    )
                )

    elif tool_name == "apply_patch":
        from kitt.edit_format.parser import SearchReplaceParser
        patch_text = str(normalized_args.get("patch", ""))
        blocks = SearchReplaceParser().parse(patch_text)
        seen_paths: set[str] = set()
        for block in blocks:
            raw_path = str(block.file_path or "").strip()
            if not raw_path or raw_path in seen_paths:
                continue
            seen_paths.add(raw_path)
            try:
                rel_path = fs.relative(raw_path)
                target = root / rel_path
                if target.is_file() and not target.is_symlink():
                    data = fs.read(rel_path)
                    preconditions.append(
                        MutationPrecondition(
                            path=rel_path,
                            expected_exists=True,
                            expected_sha256=data.sha256,
                            size=data.size,
                            mtime_ns=data.mtime_ns,
                        )
                    )
                else:
                    preconditions.append(
                        MutationPrecondition(
                            path=rel_path,
                            expected_exists=False,
                            expected_sha256=None,
                        )
                    )
            except Exception:
                preconditions.append(
                    MutationPrecondition(
                        path=raw_path,
                        expected_exists=False,
                        expected_sha256=None,
                    )
                )

    return preconditions


def validate_preconditions(
    root_dir: str | Path,
    preconditions: List[MutationPrecondition],
) -> Tuple[bool, Optional[str]]:
    """Verify that current workspace target states strictly match captured preconditions."""
    root = Path(root_dir).resolve()
    fs = WorkspaceFileSystem(root)

    for prec in preconditions:
        try:
            rel = fs.relative(prec.path)
            target = root / rel
            exists = target.exists() or target.is_symlink()

            if prec.expected_exists:
                if not exists:
                    return False, f"Approval invalidated: File '{prec.path}' was removed after approval request."
                if target.is_symlink() or not target.is_file():
                    return False, f"Approval invalidated: File '{prec.path}' is no longer a regular file."
                data = fs.read(rel)
                if data.sha256 != prec.expected_sha256:
                    return False, f"Approval invalidated: File '{prec.path}' was modified after approval request."
            else:
                if exists:
                    return False, f"Approval invalidated: File '{prec.path}' was created after approval request."
        except Exception as exc:
            return False, f"Approval invalidated: failed to verify precondition for '{prec.path}': {exc}"

    return True, None
