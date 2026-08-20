"""Race-resistant workspace file IO for tool writes and reads."""
from __future__ import annotations

import hashlib
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

FORBIDDEN_PARTS = frozenset({".git", ".env"})
DEFAULT_MAX_FILE_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class WorkspaceFileData:
    rel_path: str
    content: bytes
    size: int
    mtime_ns: int
    sha256: str


class WorkspaceFileSystem:
    def __init__(self, root_dir: str | Path, max_file_bytes: int = DEFAULT_MAX_FILE_BYTES):
        self.root = Path(root_dir).expanduser().resolve()
        self.max_file_bytes = max(1024, int(max_file_bytes))

    @staticmethod
    def _normalize(rel: str | Path) -> tuple[str, ...]:
        raw = str(rel or "").replace("\\", "/")
        path = Path(raw)
        if path.is_absolute():
            raise PermissionError("Absolute workspace paths are forbidden")
        parts = tuple(part for part in path.parts if part not in {"", "."})
        if not parts:
            return ()
        if any(part == ".." for part in parts):
            raise PermissionError("Parent traversal is forbidden")
        for part in parts:
            if part in FORBIDDEN_PARTS or part.startswith(".env"):
                raise PermissionError(f"Protected workspace path component: {part}")
        return parts

    def relative(self, rel: str | Path) -> str:
        parts = self._normalize(rel)
        return "/".join(parts) if parts else "."

    def _windows_path(self, parts: tuple[str, ...], *, allow_missing: bool = False) -> Path:
        target = self.root.joinpath(*parts)
        # Check every existing component without following a final reparse/symlink.
        current = self.root
        for part in parts:
            current = current / part
            if current.exists() or current.is_symlink():
                if current.is_symlink():
                    raise PermissionError(f"Workspace symlink/reparse traversal refused: {current}")
        resolved_parent = target.parent.resolve(strict=False)
        if not resolved_parent.is_relative_to(self.root):
            raise PermissionError("Workspace containment violation")
        if not allow_missing and not target.exists():
            raise FileNotFoundError(str(target))
        return target

    def _open_root_fd(self) -> int:
        flags = os.O_RDONLY | int(getattr(os, "O_DIRECTORY", 0))
        flags |= int(getattr(os, "O_CLOEXEC", 0))
        return os.open(str(self.root), flags)

    def _open_parent_posix(self, parts: tuple[str, ...], create: bool) -> tuple[int, str]:
        if not parts:
            raise IsADirectoryError("Workspace root is not a file")
        fd = self._open_root_fd()
        try:
            for component in parts[:-1]:
                flags = os.O_RDONLY | int(getattr(os, "O_DIRECTORY", 0))
                flags |= int(getattr(os, "O_NOFOLLOW", 0))
                flags |= int(getattr(os, "O_CLOEXEC", 0))
                try:
                    next_fd = os.open(component, flags, dir_fd=fd)
                except FileNotFoundError:
                    if not create:
                        raise
                    os.mkdir(component, 0o755, dir_fd=fd)
                    next_fd = os.open(component, flags, dir_fd=fd)
                st = os.fstat(next_fd)
                if not stat.S_ISDIR(st.st_mode):
                    os.close(next_fd)
                    raise PermissionError(f"Workspace component is not a directory: {component}")
                os.close(fd)
                fd = next_fd
            return fd, parts[-1]
        except Exception:
            os.close(fd)
            raise

    @staticmethod
    def _read_fd(fd: int, max_bytes: int) -> bytes:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise PermissionError("Workspace target must be a regular file")
        if st.st_size > max_bytes:
            raise ValueError(f"Workspace file exceeds {max_bytes} bytes")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(64 * 1024, max_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"Workspace file exceeds {max_bytes} bytes")
            chunks.append(chunk)
        return b"".join(chunks)

    def read(self, rel: str | Path, *, max_bytes: int | None = None) -> WorkspaceFileData:
        parts = self._normalize(rel)
        limit = min(self.max_file_bytes, max_bytes or self.max_file_bytes)
        if os.name == "nt":
            target = self._windows_path(parts)
            st = target.lstat()
            if not stat.S_ISREG(st.st_mode):
                raise PermissionError("Workspace target must be a regular file")
            with target.open("rb") as handle:
                content = handle.read(limit + 1)
            if len(content) > limit:
                raise ValueError(f"Workspace file exceeds {limit} bytes")
            after = target.lstat()
            if (st.st_size, st.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise PermissionError("Workspace file changed while reading")
        else:
            parent_fd, name = self._open_parent_posix(parts, create=False)
            try:
                flags = os.O_RDONLY | int(getattr(os, "O_NOFOLLOW", 0))
                flags |= int(getattr(os, "O_NONBLOCK", 0)) | int(getattr(os, "O_CLOEXEC", 0))
                fd = os.open(name, flags, dir_fd=parent_fd)
                try:
                    st = os.fstat(fd)
                    content = self._read_fd(fd, limit)
                    after = os.fstat(fd)
                    if (
                        st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns
                    ) != (
                        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
                    ):
                        raise PermissionError("Workspace file changed while reading")
                finally:
                    os.close(fd)
            finally:
                os.close(parent_fd)
        return WorkspaceFileData(
            rel_path="/".join(parts),
            content=content,
            size=st.st_size,
            mtime_ns=st.st_mtime_ns,
            sha256=hashlib.sha256(content).hexdigest(),
        )

    def exists_regular(self, rel: str | Path) -> bool:
        try:
            self.read(rel, max_bytes=self.max_file_bytes)
            return True
        except (FileNotFoundError, IsADirectoryError):
            return False

    def atomic_write(
        self,
        rel: str | Path,
        content: bytes | str,
        *,
        expected_sha256: str | None = None,
        max_bytes: int | None = None,
    ) -> str:
        parts = self._normalize(rel)
        payload = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        limit = min(self.max_file_bytes, max_bytes or self.max_file_bytes)
        if len(payload) > limit:
            raise ValueError(f"Workspace write exceeds {limit} bytes")
        digest = hashlib.sha256(payload).hexdigest()

        if os.name == "nt":
            target = self._windows_path(parts, allow_missing=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            # Revalidate parent after creation.
            self._windows_path(parts, allow_missing=True)
            if target.exists() or target.is_symlink():
                if target.is_symlink() or not target.is_file():
                    raise PermissionError("Refusing unsafe workspace write target")
                if expected_sha256 is not None:
                    current = self.read(rel).sha256
                    if current != expected_sha256:
                        raise ValueError("expected_content_hash mismatch")
            elif expected_sha256 is not None:
                raise ValueError("expected_content_hash mismatch")
            tmp = target.parent / f".{target.name}.{secrets.token_hex(8)}.tmp"
            try:
                with tmp.open("xb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp, target)
            finally:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
            return digest

        parent_fd, name = self._open_parent_posix(parts, create=True)
        temp_name = f".{name}.{secrets.token_hex(8)}.tmp"
        temp_fd = -1
        try:
            try:
                current_fd = os.open(
                    name,
                    os.O_RDONLY | int(getattr(os, "O_NOFOLLOW", 0)) | int(getattr(os, "O_CLOEXEC", 0)),
                    dir_fd=parent_fd,
                )
            except FileNotFoundError:
                current_fd = None
            if current_fd is not None:
                try:
                    current_st = os.fstat(current_fd)
                    if not stat.S_ISREG(current_st.st_mode):
                        raise PermissionError("Refusing non-regular workspace write target")
                    if expected_sha256 is not None:
                        current = self._read_fd(current_fd, limit)
                        if hashlib.sha256(current).hexdigest() != expected_sha256:
                            raise ValueError("expected_content_hash mismatch")
                finally:
                    os.close(current_fd)
            elif expected_sha256 is not None:
                raise ValueError("expected_content_hash mismatch")

            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | int(getattr(os, "O_CLOEXEC", 0))
            temp_fd = os.open(temp_name, flags, 0o600, dir_fd=parent_fd)
            offset = 0
            while offset < len(payload):
                written = os.write(temp_fd, payload[offset:])
                if written <= 0:
                    raise OSError("Short workspace write")
                offset += written
            os.fsync(temp_fd)
            os.close(temp_fd)
            temp_fd = -1

            # Revalidate final target immediately before replace.
            try:
                final_st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                final_st = None
            if final_st is not None and not stat.S_ISREG(final_st.st_mode):
                raise PermissionError("Refusing unsafe workspace replace target")
            os.replace(temp_name, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            os.fsync(parent_fd)
            return digest
        finally:
            if temp_fd >= 0:
                os.close(temp_fd)
            try:
                os.unlink(temp_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            except OSError:
                pass
            os.close(parent_fd)

    def unlink(self, rel: str | Path) -> bool:
        parts = self._normalize(rel)
        if os.name == "nt":
            target = self._windows_path(parts)
            st = target.lstat()
            if not stat.S_ISREG(st.st_mode):
                raise PermissionError("Only regular workspace files may be deleted")
            target.unlink()
            return True
        parent_fd, name = self._open_parent_posix(parts, create=False)
        try:
            st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISREG(st.st_mode):
                raise PermissionError("Only regular workspace files may be deleted")
            os.unlink(name, dir_fd=parent_fd)
            os.fsync(parent_fd)
            return True
        finally:
            os.close(parent_fd)

    def list_regular_files(self, rel: str | Path = ".", *, limit: int = 100) -> list[str]:
        parts = self._normalize(rel)
        base_rel = "/".join(parts)
        if os.name == "nt":
            target = self.root.joinpath(*parts)
            if target.is_symlink() or not target.is_dir():
                raise NotADirectoryError(str(target))
            results = []
            for entry in target.iterdir():
                if entry.is_symlink() or not entry.is_file():
                    continue
                results.append(f"{base_rel}/{entry.name}".strip("/"))
                if len(results) >= limit:
                    break
            return results

        fd = self._open_root_fd()
        try:
            for component in parts:
                next_fd = os.open(
                    component,
                    os.O_RDONLY | int(getattr(os, "O_DIRECTORY", 0)) |
                    int(getattr(os, "O_NOFOLLOW", 0)) | int(getattr(os, "O_CLOEXEC", 0)),
                    dir_fd=fd,
                )
                os.close(fd)
                fd = next_fd
            results = []
            for name in os.listdir(fd):
                try:
                    st = os.stat(name, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if stat.S_ISREG(st.st_mode):
                    results.append(f"{base_rel}/{name}".strip("/"))
                    if len(results) >= limit:
                        break
            return results
        finally:
            os.close(fd)
