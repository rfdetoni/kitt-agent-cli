from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple


@dataclass(frozen=True)
class EndpointMetadata:
    transport_type: str  # "unix" | "tcp" | "named_pipe"
    address: str
    port: Optional[int] = None
    pid: Optional[int] = None


class IPCTransport:
    """Manages cross-platform IPC socket / named pipe transport endpoints."""

    def __init__(self, root_dir: Path | str):
        self.root = Path(root_dir).resolve()
        self.kitt_dir = self.root / ".kitt"
        self.kitt_dir.mkdir(parents=True, exist_ok=True)
        self.socket_path = self.kitt_dir / "daemon.sock"
        self.endpoint_file = self.kitt_dir / "daemon_endpoint.json"
        self.pid_file = self.kitt_dir / "daemon.pid"
        self.token_file = self.kitt_dir / "daemon_token"

    def get_server_endpoint(self) -> Tuple[str, str, Optional[int]]:
        """Return (transport_type, address, port)."""
        if sys.platform != "win32":
            return ("unix", str(self.socket_path), None)
        else:
            # Loopback fallback on Windows
            return ("tcp", "127.0.0.1", 0)

    def write_endpoint_metadata(self, transport_type: str, address: str, port: Optional[int] = None) -> None:
        meta = {
            "transport_type": transport_type,
            "address": address,
            "port": port,
            "pid": os.getpid(),
        }
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        mode = 0o600
        fd = os.open(str(self.endpoint_file), flags, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(meta, f)

    def read_endpoint_metadata(self) -> Optional[EndpointMetadata]:
        if not self.endpoint_file.exists():
            if sys.platform != "win32" and self.socket_path.exists():
                return EndpointMetadata(transport_type="unix", address=str(self.socket_path), pid=self.read_pid())
            return None
        try:
            data = json.loads(self.endpoint_file.read_text(encoding="utf-8"))
            return EndpointMetadata(
                transport_type=data.get("transport_type", "unix"),
                address=data.get("address", str(self.socket_path)),
                port=data.get("port"),
                pid=data.get("pid"),
            )
        except Exception:
            return None

    def write_pid(self, pid: int) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(str(self.pid_file), flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(str(pid))

    def read_pid(self) -> Optional[int]:
        if not self.pid_file.exists():
            return None
        try:
            return int(self.pid_file.read_text(encoding="utf-8").strip())
        except Exception:
            return None

    def cleanup(self) -> None:
        for p in (self.socket_path, self.endpoint_file, self.pid_file):
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass
