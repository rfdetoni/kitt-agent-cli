from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple


_MAX_SECRET_BYTES = 4096
_MAX_ENDPOINT_BYTES = 16 * 1024
_MAX_PID_BYTES = 64
_MAX_LOCK_BYTES = 64


@dataclass(frozen=True)
class EndpointMetadata:
    transport_type: str
    address: str
    port: Optional[int] = None
    pid: Optional[int] = None


class IPCTransport:
    """Workspace-local daemon IPC state with a fail-closed filesystem boundary."""

    def __init__(self, root_dir):
        self.root = Path(root_dir).resolve()
        self.kitt_dir = self.root / ".kitt"
        self._ensure_kitt_dir()
        self.socket_path = self.kitt_dir / "daemon.sock"
        self.endpoint_file = self.kitt_dir / "daemon_endpoint.json"
        self.pid_file = self.kitt_dir / "daemon.pid"
        self.lock_file = self.kitt_dir / "daemon.lock"
        self.token_file = self.kitt_dir / "daemon.token"

    @staticmethod
    def _read_flags() -> int:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_NONBLOCK"):
            flags |= os.O_NONBLOCK
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        return flags

    @staticmethod
    def _write_flags(*, exclusive: bool = False) -> int:
        # Do not use O_TRUNC before validating the opened inode.
        flags = os.O_WRONLY | os.O_CREAT
        if exclusive:
            flags |= os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_NONBLOCK"):
            flags |= os.O_NONBLOCK
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        return flags

    def _open_kitt_dir_fd(self, *, create: bool = True) -> int:
        """Open and validate .kitt without following a repository symlink."""
        if create:
            try:
                os.mkdir(str(self.kitt_dir), 0o700)
            except FileExistsError:
                pass

        # O_NOFOLLOW is the authoritative check where available. The
        # pre-check is retained for platforms that do not expose it.
        if self.kitt_dir.is_symlink():
            raise PermissionError(
                f"Refusing symlink KITT state directory: {self.kitt_dir}"
            )

        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC

        try:
            fd = os.open(str(self.kitt_dir), flags)
        except OSError as exc:
            raise PermissionError(
                f"Unable to securely open KITT state directory "
                f"{self.kitt_dir}: {exc}"
            ) from exc

        try:
            st = os.fstat(fd)
            if not stat.S_ISDIR(st.st_mode):
                raise PermissionError(
                    f"KITT state path is not a directory: {self.kitt_dir}"
                )
            if sys.platform != "win32":
                if st.st_uid != os.getuid():
                    raise PermissionError(
                        "KITT state directory owner mismatch"
                    )
                try:
                    os.fchmod(fd, 0o700)
                except OSError as exc:
                    raise PermissionError(
                        "Unable to secure KITT state directory permissions"
                    ) from exc
                st = os.fstat(fd)
                if stat.S_IMODE(st.st_mode) & 0o077:
                    raise PermissionError(
                        "KITT state directory permissions must be 0700"
                    )
            return fd
        except Exception:
            os.close(fd)
            raise

    def _ensure_kitt_dir(self) -> None:
        fd = self._open_kitt_dir_fd(create=True)
        os.close(fd)

    def _is_internal(self, path: Path) -> bool:
        try:
            return Path(path).absolute().parent == self.kitt_dir.absolute()
        except Exception:
            return False

    def _open_internal(
        self,
        path: Path,
        flags: int,
        mode: int = 0o600,
    ) -> tuple[int, Optional[int]]:
        """Open a daemon state file relative to a validated directory FD."""
        if not self._is_internal(path):
            return os.open(str(path), flags, mode), None

        dir_fd = self._open_kitt_dir_fd(create=True)
        try:
            # dir_fd prevents a post-validation swap of `.kitt`.
            fd = os.open(path.name, flags, mode, dir_fd=dir_fd)
            return fd, dir_fd
        except Exception:
            os.close(dir_fd)
            raise

    @staticmethod
    def _close_pair(fd: int, dir_fd: Optional[int]) -> None:
        try:
            os.close(fd)
        finally:
            if dir_fd is not None:
                os.close(dir_fd)

    @staticmethod
    def _validate_regular_fd(
        fd: int,
        path: Path,
        *,
        require_private: bool,
    ) -> os.stat_result:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise PermissionError(
                f"Daemon state must be a regular file: {path}"
            )
        if sys.platform != "win32":
            if st.st_uid != os.getuid():
                raise PermissionError(
                    f"Daemon state owner mismatch: {path}"
                )
            if require_private and stat.S_IMODE(st.st_mode) & 0o077:
                raise PermissionError(
                    f"Daemon state permissions must be 0600: {path}"
                )
        return st

    def secure_write(
        self,
        path: Path,
        content: str,
        *,
        exclusive: bool = False,
    ) -> None:
        path = Path(path)
        if path.is_symlink():
            raise PermissionError(f"Refusing symlink daemon state: {path}")

        flags = self._write_flags(exclusive=exclusive)
        try:
            fd, dir_fd = self._open_internal(path, flags, 0o600)
        except OSError as exc:
            raise PermissionError(
                f"Unable to securely open daemon state {path}: {exc}"
            ) from exc
        try:
            self._validate_regular_fd(
                fd,
                path,
                require_private=False,
            )
            if sys.platform != "win32":
                os.fchmod(fd, 0o600)

            # Validate first, truncate second. A FIFO/device/symlink is never
            # modified as a side effect of validation.
            os.ftruncate(fd, 0)
            data = content.encode("utf-8")
            offset = 0
            while offset < len(data):
                written = os.write(fd, data[offset:])
                if written <= 0:
                    raise OSError("Short daemon state write")
                offset += written
            os.fsync(fd)
        finally:
            self._close_pair(fd, dir_fd)

    def _secure_read_text(
        self,
        path: Path,
        *,
        max_bytes: int,
        require_private: bool = True,
    ) -> str:
        path = Path(path)
        if path.is_symlink():
            raise PermissionError(f"Refusing symlink daemon state: {path}")

        flags = self._read_flags()
        try:
            fd, dir_fd = self._open_internal(path, flags)
        except FileNotFoundError:
            return ""
        except OSError as exc:
            raise PermissionError(
                f"Unable to securely open daemon state {path}: {exc}"
            ) from exc

        try:
            before = self._validate_regular_fd(
                fd,
                path,
                require_private=require_private,
            )
            if before.st_size > max_bytes:
                raise PermissionError(
                    f"Daemon state exceeds {max_bytes} bytes: {path}"
                )

            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(
                    fd,
                    min(4096, (max_bytes + 1) - total),
                )
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise PermissionError(
                        f"Daemon state exceeds {max_bytes} bytes: {path}"
                    )
                chunks.append(chunk)

            after = os.fstat(fd)
            fingerprint_before = (
                getattr(before, "st_dev", None),
                getattr(before, "st_ino", None),
                before.st_size,
                getattr(before, "st_mtime_ns", None),
                getattr(before, "st_ctime_ns", None),
            )
            fingerprint_after = (
                getattr(after, "st_dev", None),
                getattr(after, "st_ino", None),
                after.st_size,
                getattr(after, "st_mtime_ns", None),
                getattr(after, "st_ctime_ns", None),
            )
            if (
                fingerprint_before != fingerprint_after
                or total != before.st_size
            ):
                raise PermissionError(
                    f"Daemon state changed while being read: {path}"
                )

            return b"".join(chunks).decode("utf-8")
        finally:
            self._close_pair(fd, dir_fd)

    def read_secret(self, path: Path) -> str:
        return self._secure_read_text(
            path,
            max_bytes=_MAX_SECRET_BYTES,
            require_private=True,
        ).strip()

    def get_server_endpoint(self) -> Tuple[str, str, Optional[int]]:
        if sys.platform != "win32":
            return ("unix", str(self.socket_path), None)
        return ("tcp", "127.0.0.1", 0)

    def write_endpoint_metadata(
        self,
        transport_type,
        address,
        port=None,
    ):
        self.secure_write(
            self.endpoint_file,
            json.dumps(
                {
                    "transport_type": transport_type,
                    "address": address,
                    "port": port,
                    "pid": os.getpid(),
                }
            ),
        )

    def read_endpoint_metadata(self):
        try:
            raw = self._secure_read_text(
                self.endpoint_file,
                max_bytes=_MAX_ENDPOINT_BYTES,
                require_private=True,
            )
            if not raw:
                if (
                    sys.platform != "win32"
                    and self.socket_path.exists()
                    and not self.socket_path.is_symlink()
                ):
                    return EndpointMetadata(
                        "unix",
                        str(self.socket_path),
                        pid=self.read_pid(),
                    )
                return None

            data = json.loads(raw)
            if not isinstance(data, dict):
                return None

            transport_type = data.get("transport_type")
            if transport_type not in {"unix", "tcp"}:
                return None

            address = data.get("address")
            if not isinstance(address, str) or not address or len(address) > 4096:
                return None

            port = data.get("port")
            if port is not None:
                if (
                    isinstance(port, bool)
                    or not isinstance(port, int)
                    or port < 1
                    or port > 65535
                ):
                    return None

            pid = data.get("pid")
            if pid is not None:
                if (
                    isinstance(pid, bool)
                    or not isinstance(pid, int)
                    or pid <= 0
                ):
                    return None

            if transport_type == "tcp":
                # TCP daemon transport is loopback-only.
                if address not in {"127.0.0.1", "::1", "localhost"}:
                    return None
                if port is None:
                    return None
            elif sys.platform != "win32":
                # Default Unix endpoint may not escape the validated .kitt dir.
                try:
                    endpoint_path = Path(address).absolute()
                    endpoint_path.relative_to(self.kitt_dir.absolute())
                except (OSError, ValueError):
                    return None

            return EndpointMetadata(
                transport_type,
                address,
                port,
                pid,
            )
        except Exception:
            return None

    def write_pid(self, pid: int):
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise ValueError("Daemon PID must be a positive integer")
        self.secure_write(self.pid_file, str(pid))

    def read_pid(self):
        try:
            raw = self._secure_read_text(
                self.pid_file,
                max_bytes=_MAX_PID_BYTES,
                require_private=True,
            ).strip()
            if not raw:
                return None
            pid = int(raw)
            return pid if pid > 0 else None
        except Exception:
            return None

    def acquire_instance_lock(self) -> int:
        flags = self._write_flags(exclusive=True)
        try:
            fd, dir_fd = self._open_internal(
                self.lock_file,
                flags,
                0o600,
            )
            # The lock FD must outlive this method, but the directory FD need
            # not. The opened inode remains pinned.
            if dir_fd is not None:
                os.close(dir_fd)
            self._validate_regular_fd(
                fd,
                self.lock_file,
                require_private=False,
            )
            if sys.platform != "win32":
                os.fchmod(fd, 0o600)
            os.write(fd, str(os.getpid()).encode("ascii"))
            os.fsync(fd)
            return fd
        except FileExistsError:
            try:
                raw = self._secure_read_text(
                    self.lock_file,
                    max_bytes=_MAX_LOCK_BYTES,
                    require_private=True,
                ).strip()
                pid = int(raw) if raw else None
            except Exception:
                pid = None

            if pid and pid > 0:
                try:
                    os.kill(pid, 0)
                except OSError:
                    pass
                else:
                    raise RuntimeError(
                        f"KITT daemon already running (PID {pid})"
                    )

            # Remove only the directory entry under the validated .kitt FD.
            self._unlink_internal(self.lock_file)
            fd, dir_fd = self._open_internal(
                self.lock_file,
                flags,
                0o600,
            )
            if dir_fd is not None:
                os.close(dir_fd)
            self._validate_regular_fd(
                fd,
                self.lock_file,
                require_private=False,
            )
            if sys.platform != "win32":
                os.fchmod(fd, 0o600)
            os.write(fd, str(os.getpid()).encode("ascii"))
            os.fsync(fd)
            return fd

    def _unlink_internal(self, path: Path) -> None:
        path = Path(path)
        if not self._is_internal(path):
            path.unlink(missing_ok=True)
            return

        dir_fd = self._open_kitt_dir_fd(create=False)
        try:
            try:
                os.unlink(path.name, dir_fd=dir_fd)
            except FileNotFoundError:
                pass
        finally:
            os.close(dir_fd)

    def release_instance_lock(self, fd: Optional[int]) -> None:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        self._unlink_internal(self.lock_file)

    def cleanup(self):
        # If `.kitt` was replaced by a symlink after construction, the
        # validated-directory open fails and nothing outside the workspace is
        # touched.
        for path in (
            self.socket_path,
            self.endpoint_file,
            self.pid_file,
            self.lock_file,
        ):
            try:
                self._unlink_internal(path)
            except (OSError, PermissionError):
                # Cleanup is best-effort, but never follows an unsafe parent.
                continue
