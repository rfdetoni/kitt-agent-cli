"""Safe workspace listing and mutation helpers for compact runtime operations."""
from __future__ import annotations

import hashlib
import os
import shutil
import stat
import threading
from pathlib import Path
from typing import Any

from kitt.security.workspace_fs import WorkspaceFileSystem

_MUTATION_LOCK = threading.RLock()


def _lstat_safe(fs: WorkspaceFileSystem, rel: str) -> tuple[Path, os.stat_result]:
    path = fs.absolute_lexical(rel)
    st = path.lstat()
    if stat.S_ISLNK(st.st_mode) or fs._windows_reparse_point(st):
        raise PermissionError(f"Workspace symlink/junction/reparse target refused: {rel}")
    return path, st


def _assert_real_directory(fs: WorkspaceFileSystem, rel: str) -> Path:
    normalized = fs.relative(rel)
    if not fs.is_safe_directory(normalized):
        raise NotADirectoryError(normalized)
    return fs.absolute_lexical(normalized)


def list_entries(
    fs: WorkspaceFileSystem,
    rel: str | Path = ".",
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List immediate regular files and real directories without following links."""
    relative = fs.relative(rel)
    base = _assert_real_directory(fs, relative)
    bounded = max(1, min(int(limit), 500))
    entries: list[dict[str, Any]] = []
    with os.scandir(base) as iterator:
        for entry in sorted(iterator, key=lambda item: item.name.casefold()):
            try:
                st = entry.stat(follow_symlinks=False)
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(st.st_mode) or fs._windows_reparse_point(st):
                continue
            if stat.S_ISDIR(st.st_mode):
                kind = "directory"
            elif stat.S_ISREG(st.st_mode):
                kind = "file"
            else:
                continue
            item = f"{relative.rstrip('/')}/{entry.name}" if relative != "." else entry.name
            entries.append({"path": item, "type": kind, "size": st.st_size if kind == "file" else None})
            if len(entries) >= bounded:
                break
    return entries


def _hash_file(fs: WorkspaceFileSystem, rel: str) -> str:
    return fs.read(rel).sha256


def move_path(
    fs: WorkspaceFileSystem,
    source: str | Path,
    destination: str | Path,
    *,
    overwrite: bool = False,
    expected_sha256: str | None = None,
    create_parents: bool = True,
) -> dict[str, Any]:
    """Move/rename one regular file or real directory inside the workspace."""
    src = fs.relative(source)
    dst = fs.relative(destination)
    if src == "." or dst == ".":
        raise PermissionError("Workspace root cannot be moved or replaced")
    if src == dst:
        return {"source": src, "destination": dst, "moved": False, "reason": "same_path"}

    src_parts = tuple(Path(src).parts)
    dst_parts = tuple(Path(dst).parts)
    if len(dst_parts) > len(src_parts) and dst_parts[: len(src_parts)] == src_parts:
        raise ValueError("Cannot move a directory into its own subtree")

    with _MUTATION_LOCK:
        src_path, src_st = _lstat_safe(fs, src)
        src_is_file = stat.S_ISREG(src_st.st_mode)
        src_is_dir = stat.S_ISDIR(src_st.st_mode)
        if not (src_is_file or src_is_dir):
            raise PermissionError("Only regular files and real directories may be moved")
        if src_is_dir and not fs.is_safe_directory(src):
            raise PermissionError("Unsafe source directory traversal refused")
        if src_is_file and expected_sha256 is not None and _hash_file(fs, src) != expected_sha256:
            raise ValueError("expected_content_hash mismatch")
        if src_is_dir and expected_sha256 is not None:
            raise ValueError("expected_content_hash is supported only for files")

        dst_parent = Path(dst).parent.as_posix()
        if dst_parent in {"", "."}:
            dst_parent = "."
        if create_parents and dst_parent != ".":
            fs.create_directory(dst_parent, parents=True, exist_ok=True)
        _assert_real_directory(fs, dst_parent)
        dst_path = fs.absolute_lexical(dst)

        try:
            dst_st = dst_path.lstat()
            dst_exists = True
        except FileNotFoundError:
            dst_st = None
            dst_exists = False
        if dst_exists:
            assert dst_st is not None
            if stat.S_ISLNK(dst_st.st_mode) or fs._windows_reparse_point(dst_st):
                raise PermissionError("Unsafe destination target refused")
            if not overwrite:
                raise FileExistsError(dst)
            if not src_is_file or not stat.S_ISREG(dst_st.st_mode):
                raise PermissionError("overwrite=true is supported only for regular file-to-file moves")

        # Revalidate the source immediately before mutation.
        final_src_st = src_path.lstat()
        if stat.S_ISLNK(final_src_st.st_mode) or fs._windows_reparse_point(final_src_st):
            raise PermissionError("Source changed to an unsafe target before move")
        old_ino = int(getattr(src_st, "st_ino", 0))
        new_ino = int(getattr(final_src_st, "st_ino", 0))
        if old_ino and new_ino and old_ino != new_ino:
            raise ValueError("workspace source identity changed before move")
        if src_is_file and expected_sha256 is not None and _hash_file(fs, src) != expected_sha256:
            raise ValueError("expected_content_hash mismatch")

        if overwrite:
            os.replace(src_path, dst_path)
        else:
            # os.rename is atomic within a filesystem. The explicit no-overwrite
            # precheck plus the process-wide lock prevents KITT-internal races.
            os.rename(src_path, dst_path)

        return {
            "source": src,
            "destination": dst,
            "moved": True,
            "type": "file" if src_is_file else "directory",
        }


def _assert_tree_has_no_links(fs: WorkspaceFileSystem, root: Path) -> None:
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in list(dirs) + list(files):
            candidate = current_path / name
            st = candidate.lstat()
            if stat.S_ISLNK(st.st_mode) or fs._windows_reparse_point(st):
                raise PermissionError(f"Recursive delete refused unsafe link/reparse point: {candidate}")


def delete_path(
    fs: WorkspaceFileSystem,
    rel: str | Path,
    *,
    recursive: bool = False,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Delete one regular file or real directory; non-empty directories require recursive=true."""
    relative = fs.relative(rel)
    if relative == ".":
        raise PermissionError("Workspace root cannot be deleted")

    with _MUTATION_LOCK:
        path, st = _lstat_safe(fs, relative)
        if stat.S_ISREG(st.st_mode):
            deleted = fs.unlink(relative, expected_sha256=expected_sha256, expected_exists=True)
            return {"path": relative, "deleted": deleted, "type": "file"}
        if not stat.S_ISDIR(st.st_mode) or not fs.is_safe_directory(relative):
            raise PermissionError("Only regular files and real directories may be deleted")
        if expected_sha256 is not None:
            raise ValueError("expected_content_hash is supported only for files")

        # Verify every descendant without following symlinks before recursive removal.
        if recursive:
            _assert_tree_has_no_links(fs, path)
            shutil.rmtree(path)
        else:
            path.rmdir()
        return {"path": relative, "deleted": True, "type": "directory", "recursive": bool(recursive)}
