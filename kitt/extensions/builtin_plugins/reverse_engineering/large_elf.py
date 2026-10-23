"""Stream ELF metadata and SHA-256 without loading a large binary into RAM.

This is a read-only inventory tool, not a disassembler or code executor.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path, PureWindowsPath
import stat

from .binary import InspectionError, _elf

MAX_ELF_BYTES = 512 * 1024 * 1024
_BLOCK_BYTES = 1024 * 1024


def _parts(relative_path: str) -> tuple[str, ...]:
    if not isinstance(relative_path, str) or not relative_path or "\0" in relative_path:
        raise InspectionError("Invalid ELF path")
    value = Path(relative_path)
    win = PureWindowsPath(relative_path)
    if value.is_absolute() or win.is_absolute() or win.drive or "\\" in relative_path:
        raise InspectionError("ELF path must be workspace-relative")
    if any(part in {"", "..", "."} for part in value.parts) or not value.parts:
        raise InspectionError("ELF path traverses or names no file")
    return tuple(value.parts)


def _open_nofollow(root: Path, parts: tuple[str, ...]) -> int:
    flags = (os.O_RDONLY | getattr(os, "O_BINARY", 0)
             | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0))
    if os.name == "posix" and os.open in os.supports_dir_fd:
        directory = os.open(root, flags | getattr(os, "O_DIRECTORY", 0))
        try:
            for part in parts[:-1]:
                next_fd = os.open(
                    part, flags | getattr(os, "O_DIRECTORY", 0),
                    dir_fd=directory,
                )
                os.close(directory)
                directory = next_fd
            return os.open(parts[-1], flags, dir_fd=directory)
        finally:
            os.close(directory)
    target = root.joinpath(*parts)
    cursor = root
    for part in parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise InspectionError("Symlinks are forbidden")
    if not target.resolve(strict=True).is_relative_to(root):
        raise InspectionError("ELF path outside workspace")
    return os.open(target, flags)


def inspect_large_elf(root: Path, relative_path: str) -> dict:
    """Hash a bounded regular ELF file and inspect its header in constant RAM."""
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise InspectionError("Workspace must be a directory")
    try:
        fd = _open_nofollow(root, _parts(relative_path))
    except (OSError, RuntimeError) as exc:
        raise InspectionError(f"Cannot open ELF: {type(exc).__name__}") from exc
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 64 <= before.st_size <= MAX_ELF_BYTES:
            raise InspectionError("Expected regular ELF file (64 bytes..512 MiB)")
        header = os.read(fd, 64)
        if len(header) != 64 or not header.startswith(b"\x7fELF"):
            raise InspectionError("Invalid or truncated ELF64/ELF32 header")
        metadata = _elf(header)
        digest = hashlib.sha256()
        digest.update(header)
        total = len(header)
        while total <= MAX_ELF_BYTES:
            block = os.read(fd, min(_BLOCK_BYTES, MAX_ELF_BYTES + 1 - total))
            if not block:
                break
            total += len(block)
            digest.update(block)
        after = os.fstat(fd)
        if (
            total != before.st_size or total > MAX_ELF_BYTES
            or (before.st_size, before.st_mtime_ns, before.st_ino, before.st_dev)
            != (after.st_size, after.st_mtime_ns, after.st_ino, after.st_dev)
        ):
            raise InspectionError("ELF changed during inventory or exceeded size limit")
        return {
            "path": relative_path, "size": total,
            "sha256": digest.hexdigest(), **metadata,
            "streamed": True, "read_only": True,
            "verification": "single_pass_stable_metadata_not_atomic_snapshot",
        }
    except OSError as exc:
        raise InspectionError(f"Cannot read ELF: {type(exc).__name__}") from exc
    finally:
        os.close(fd)
