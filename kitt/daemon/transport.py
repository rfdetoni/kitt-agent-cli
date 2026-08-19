from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple


@dataclass(frozen=True)
class EndpointMetadata:
    transport_type: str
    address: str
    port: Optional[int] = None
    pid: Optional[int] = None


class IPCTransport:
    def __init__(self, root_dir):
        self.root = Path(root_dir).resolve()
        self.kitt_dir = self.root / ".kitt"
        self.kitt_dir.mkdir(parents=True, exist_ok=True)
        if sys.platform != "win32":
            os.chmod(self.kitt_dir, 0o700)
        self.socket_path = self.kitt_dir / "daemon.sock"
        self.endpoint_file = self.kitt_dir / "daemon_endpoint.json"
        self.pid_file = self.kitt_dir / "daemon.pid"
        self.lock_file = self.kitt_dir / "daemon.lock"
        self.token_file = self.kitt_dir / "daemon.token"

    @staticmethod
    def _secure_flags(create=True, truncate=True):
        flags = os.O_WRONLY
        if create:
            flags |= os.O_CREAT
        if truncate:
            flags |= os.O_TRUNC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        return flags

    def secure_write(self, path: Path, content: str, *, exclusive=False) -> None:
        flags = self._secure_flags()
        if exclusive:
            flags |= os.O_EXCL
        fd = os.open(str(path), flags, 0o600)
        try:
            os.write(fd, content.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        if sys.platform != "win32":
            os.chmod(path, 0o600)

    def read_secret(self, path: Path) -> str:
        if not path.exists():
            return ""
        if path.is_symlink():
            raise PermissionError(f"Refusing symlink secret: {path}")
        st = path.stat()
        if sys.platform != "win32":
            if st.st_uid != os.getuid():
                raise PermissionError("Daemon secret owner mismatch")
            if stat.S_IMODE(st.st_mode) & 0o077:
                raise PermissionError("Daemon secret permissions must be 0600")
        return path.read_text(encoding="utf-8").strip()

    def get_server_endpoint(self) -> Tuple[str, str, Optional[int]]:
        return ("unix", str(self.socket_path), None) if sys.platform != "win32" else ("tcp", "127.0.0.1", 0)

    def write_endpoint_metadata(self, transport_type, address, port=None):
        self.secure_write(self.endpoint_file, json.dumps({
            "transport_type": transport_type,
            "address": address,
            "port": port,
            "pid": os.getpid(),
        }))

    def read_endpoint_metadata(self):
        if not self.endpoint_file.exists():
            if sys.platform != "win32" and self.socket_path.exists():
                return EndpointMetadata("unix", str(self.socket_path), pid=self.read_pid())
            return None
        try:
            data = json.loads(self.endpoint_file.read_text(encoding="utf-8"))
            return EndpointMetadata(
                data.get("transport_type", "unix"),
                data.get("address", str(self.socket_path)),
                data.get("port"),
                data.get("pid"),
            )
        except Exception:
            return None

    def write_pid(self, pid: int):
        self.secure_write(self.pid_file, str(pid))

    def read_pid(self):
        try:
            return int(self.pid_file.read_text(encoding="utf-8").strip())
        except Exception:
            return None

    def acquire_instance_lock(self) -> int:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(str(self.lock_file), flags, 0o600)
            os.write(fd, str(os.getpid()).encode())
            return fd
        except FileExistsError:
            try:
                pid = int(self.lock_file.read_text(encoding="utf-8").strip())
            except Exception:
                pid = self.read_pid()
            if pid:
                try:
                    os.kill(pid, 0)
                except OSError:
                    pass
                else:
                    raise RuntimeError(f"KITT daemon already running (PID {pid})")
            self.lock_file.unlink(missing_ok=True)
            fd = os.open(str(self.lock_file), flags, 0o600)
            os.write(fd, str(os.getpid()).encode())
            return fd

    def release_instance_lock(self, fd: Optional[int]) -> None:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        self.lock_file.unlink(missing_ok=True)

    def cleanup(self):
        for p in (self.socket_path, self.endpoint_file, self.pid_file, self.lock_file):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
