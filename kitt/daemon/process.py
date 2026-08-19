from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Optional

from kitt.daemon.client import DaemonClient
from kitt.daemon.server import DaemonServer
from kitt.daemon.transport import IPCTransport


def run_daemon_foreground(workspace: str) -> None:
    """Run daemon in foreground (used by supervisor or sub-process worker)."""
    server = DaemonServer(workspace_root=workspace)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(server.start())
        print(f"K.I.T.T. Daemon running in foreground on {server.socket_path}")
        loop.run_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        loop.run_until_complete(server.stop())
        loop.close()


def start_daemon_detached(workspace: str, timeout_seconds: float = 10.0) -> Dict[str, Any]:
    """Spawn detached background daemon and wait for readiness handshake."""
    transport = IPCTransport(workspace)
    existing_pid = transport.read_pid()

    # Check if existing daemon is alive
    if existing_pid:
        try:
            os.kill(existing_pid, 0)
            # Process is running
            client = DaemonClient(workspace_root=workspace)
            if asyncio.run(client.is_running()):
                return {"status": "ok", "message": f"Daemon is already running (PID {existing_pid})", "pid": existing_pid}
        except OSError:
            # Stale PID file
            transport.cleanup()

    # Spawn background daemon process
    cmd = [
        sys.executable,
        "-m",
        "kitt.cli.main",
        "daemon",
        "run",
        "--workspace",
        str(Path(workspace).resolve()),
    ]

    kwargs: Dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "cwd": str(Path(workspace).resolve()),
    }

    if sys.platform != "win32":
        kwargs["start_new_session"] = True
    else:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

    proc = subprocess.Popen(cmd, **kwargs)

    # Readiness handshake: poll until daemon responds to ping
    client = DaemonClient(workspace_root=workspace)
    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        try:
            if asyncio.run(client.is_running()):
                pid = transport.read_pid() or proc.pid
                return {"status": "ok", "message": f"Daemon started successfully in background (PID {pid})", "pid": pid}
        except Exception:
            pass
        time.sleep(0.1)

    return {"status": "error", "error": f"Daemon failed to report readiness within {timeout_seconds}s", "pid": proc.pid}


def stop_daemon(workspace: str) -> Dict[str, Any]:
    """Stop running background daemon."""
    transport = IPCTransport(workspace)
    client = DaemonClient(workspace_root=workspace)

    try:
        if asyncio.run(client.is_running()):
            resp = asyncio.run(client.stop_daemon())
            transport.cleanup()
            return {"status": "ok", "message": "Daemon stopped via IPC"}
    except Exception:
        pass

    pid = transport.read_pid()
    if pid:
        try:
            os.kill(pid, 15)  # SIGTERM
            transport.cleanup()
            return {"status": "ok", "message": f"Sent SIGTERM to daemon (PID {pid})"}
        except OSError as exc:
            transport.cleanup()
            return {"status": "error", "error": f"Could not stop PID {pid}: {exc}"}

    return {"status": "error", "error": "Daemon is not running"}


def get_daemon_status(workspace: str) -> Dict[str, Any]:
    """Inspect daemon status."""
    transport = IPCTransport(workspace)
    client = DaemonClient(workspace_root=workspace)
    running = asyncio.run(client.is_running())
    pid = transport.read_pid()
    endpoint = transport.read_endpoint_metadata()

    return {
        "running": running,
        "pid": pid,
        "transport": endpoint.transport_type if endpoint else ("unix" if sys.platform != "win32" else "tcp"),
        "address": endpoint.address if endpoint else str(transport.socket_path),
        "port": endpoint.port if endpoint else None,
    }
