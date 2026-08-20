"""Private KITT state utilities.

Security-sensitive and user-private runtime state must never derive authority
from files inside the repository checkout.  State is keyed by canonical
workspace identity and stored below ~/.kitt/workspaces by default.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import stat
import threading
from pathlib import Path
from typing import Any, Iterator, Optional

from kitt.security.credentials import atomic_write_secure

_MAX_JSON_BYTES = 4 * 1024 * 1024
_thread_locks: dict[str, threading.RLock] = {}
_thread_locks_guard = threading.Lock()


def _absolute_unresolved(path: str | Path) -> Path:
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def kitt_home() -> Path:
    configured = os.environ.get("KITT_HOME")
    path = _absolute_unresolved(configured or (Path.home() / ".kitt"))
    return ensure_private_dir(path)


def _validate_private_dir(path: Path) -> None:
    try:
        st = path.lstat()
    except FileNotFoundError as exc:
        raise PermissionError(f"Private state directory does not exist: {path}") from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise PermissionError(f"Private state path must be a real directory: {path}")
    if os.name != "nt":
        if st.st_uid != os.getuid():
            raise PermissionError(f"Private state directory owner mismatch: {path}")
        if stat.S_IMODE(st.st_mode) & 0o077:
            raise PermissionError(f"Private state directory must not be group/world accessible: {path}")


def ensure_private_dir(path: str | Path) -> Path:
    path = _absolute_unresolved(path)
    chain: list[Path] = []
    current = path
    while not current.exists() and current.parent != current:
        chain.append(current)
        current = current.parent
    anchor = current

    for candidate in reversed(chain):
        parent = candidate.parent
        if parent.is_symlink():
            raise PermissionError(f"Refusing symlink private-state component: {parent}")
        candidate.mkdir(mode=0o700, exist_ok=True)

    cursor = path
    secure_chain: list[Path] = []
    while cursor != anchor:
        secure_chain.append(cursor)
        cursor = cursor.parent
    secure_chain.reverse()

    for candidate in secure_chain:
        try:
            st = candidate.lstat()
        except FileNotFoundError as exc:
            raise PermissionError(f"Private-state component missing: {candidate}") from exc
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise PermissionError(f"Private-state component is not a real directory: {candidate}")
        if os.name != "nt":
            if st.st_uid != os.getuid():
                raise PermissionError(f"Private-state component owner mismatch: {candidate}")
            try:
                os.chmod(candidate, 0o700)
            except OSError as exc:
                raise PermissionError(f"Unable to secure private-state directory {candidate}: {exc}") from exc
    _validate_private_dir(path)
    return path


def workspace_key(root_dir: str | Path) -> str:
    canonical = str(Path(root_dir).expanduser().resolve(strict=False))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def workspace_state_dir(root_dir: str | Path, *parts: str) -> Path:
    base = ensure_private_dir(kitt_home() / "workspaces")
    workspace = ensure_private_dir(base / workspace_key(root_dir))
    current = workspace
    for part in parts:
        if not part or part in {".", ".."} or "/" in part or "\\" in part:
            raise ValueError(f"Invalid private-state component: {part!r}")
        current = ensure_private_dir(current / part)
    return current


def secure_read_bytes(
    path: str | Path,
    *,
    max_bytes: int = _MAX_JSON_BYTES,
    require_private: bool = True,
) -> bytes:
    path = _absolute_unresolved(path)
    if path.is_symlink():
        raise PermissionError(f"Refusing symlink private-state file: {path}")
    flags = os.O_RDONLY
    for name in ("O_NOFOLLOW", "O_NONBLOCK", "O_CLOEXEC"):
        flags |= int(getattr(os, name, 0))
    try:
        fd = os.open(str(path), flags)
    except FileNotFoundError:
        raise
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise PermissionError(f"Private-state file must be regular: {path}")
        if require_private and os.name != "nt":
            if st.st_uid != os.getuid():
                raise PermissionError(f"Private-state file owner mismatch: {path}")
            if stat.S_IMODE(st.st_mode) & 0o077:
                raise PermissionError(f"Private-state file must be 0600: {path}")
        if st.st_size > max_bytes:
            raise ValueError(f"Private-state file exceeds {max_bytes} bytes: {path}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(64 * 1024, max_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"Private-state file exceeds {max_bytes} bytes: {path}")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def secure_read_text(path: str | Path, *, max_bytes: int = _MAX_JSON_BYTES) -> str:
    return secure_read_bytes(path, max_bytes=max_bytes).decode("utf-8")


def secure_read_json(
    path: str | Path,
    *,
    default: Any = None,
    max_bytes: int = _MAX_JSON_BYTES,
) -> Any:
    try:
        raw = secure_read_text(path, max_bytes=max_bytes)
    except FileNotFoundError:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def secure_write_text(path: str | Path, content: str, *, max_bytes: int = _MAX_JSON_BYTES) -> None:
    payload = content.encode("utf-8")
    if len(payload) > max_bytes:
        raise ValueError(f"Private-state payload exceeds {max_bytes} bytes")
    target = _absolute_unresolved(path)
    ensure_private_dir(target.parent)
    atomic_write_secure(target, content)


def secure_write_json(path: str | Path, value: Any, *, max_bytes: int = _MAX_JSON_BYTES) -> None:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    secure_write_text(path, payload, max_bytes=max_bytes)


class InterProcessFileLock:
    """Small cross-platform exclusive advisory lock on a private regular file."""

    def __init__(self, path: str | Path):
        self.path = _absolute_unresolved(path)
        self._fd: Optional[int] = None
        key = str(self.path)
        with _thread_locks_guard:
            self._thread_lock = _thread_locks.setdefault(key, threading.RLock())

    def __enter__(self):
        self._thread_lock.acquire()
        try:
            ensure_private_dir(self.path.parent)
            if self.path.is_symlink():
                raise PermissionError(f"Refusing symlink lock file: {self.path}")
            flags = os.O_RDWR | os.O_CREAT
            for name in ("O_NOFOLLOW", "O_CLOEXEC"):
                flags |= int(getattr(os, name, 0))
            self._fd = os.open(str(self.path), flags, 0o600)
            st = os.fstat(self._fd)
            if not stat.S_ISREG(st.st_mode):
                raise PermissionError(f"Lock target must be regular: {self.path}")
            if os.name != "nt":
                os.fchmod(self._fd, 0o600)
                import fcntl
                fcntl.flock(self._fd, fcntl.LOCK_EX)
            else:
                import msvcrt
                os.lseek(self._fd, 0, os.SEEK_SET)
                try:
                    os.write(self._fd, b"\0")
                except OSError:
                    pass
                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_LOCK, 1)
            return self
        except Exception:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
            self._thread_lock.release()
            raise

    def __exit__(self, exc_type, exc, tb):
        try:
            if self._fd is not None:
                if os.name != "nt":
                    import fcntl
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
                else:
                    import msvcrt
                    os.lseek(self._fd, 0, os.SEEK_SET)
                    try:
                        msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
                os.close(self._fd)
                self._fd = None
        finally:
            self._thread_lock.release()
        return False


@contextlib.contextmanager
def locked_json_update(path: str | Path, *, default: Any, max_bytes: int = _MAX_JSON_BYTES) -> Iterator[Any]:
    target = _absolute_unresolved(path)
    with InterProcessFileLock(target.with_suffix(target.suffix + ".lock")):
        value = secure_read_json(target, default=default, max_bytes=max_bytes)
        yield value
        secure_write_json(target, value, max_bytes=max_bytes)
