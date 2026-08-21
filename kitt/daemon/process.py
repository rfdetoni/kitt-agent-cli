from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict

from kitt.daemon.client import DaemonClient
from kitt.daemon.server import DaemonServer
from kitt.daemon.transport import IPCTransport
from kitt.tools.process_runner import sanitized_subprocess_env


async def _run_daemon_server(server: DaemonServer) -> None:
    await server.start()
    try:
        # ``stop`` is an authenticated daemon request that flips _running and
        # closes the listener. Do not use loop.run_forever(), otherwise the
        # process can survive after a successful remote stop.
        while server._running:
            await asyncio.sleep(0.1)
    finally:
        if server._running or server._server is not None:
            await server.stop()


def run_daemon_foreground(workspace: str) -> None:
    server = DaemonServer(workspace_root=workspace)
    try:
        asyncio.run(_run_daemon_server(server))
    except (KeyboardInterrupt, SystemExit):
        # asyncio.run() cancels pending tasks and the coroutine's finally block
        # closes the server/runtime.
        pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False


def _terminate_spawned(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name != "nt":
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=2)
            return
        except Exception:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                pass
    else:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=sanitized_subprocess_env(),
                timeout=3,
                check=False,
            )
        except Exception:
            pass
    try:
        proc.kill()
    except Exception:
        pass


async def _probe_daemon(workspace: str) -> bool:
    client = DaemonClient(workspace_root=workspace)
    try:
        return await client.is_running()
    finally:
        await client.close()


async def _stop_daemon_via_ipc(workspace: str) -> Dict[str, Any]:
    client = DaemonClient(workspace_root=workspace)
    try:
        if not await client.is_running():
            return {"status": "error", "error": "Daemon is not running"}
        try:
            response = await client.stop_daemon()
            return {"status": "ok", "response": response}
        except (asyncio.CancelledError, ConnectionError, EOFError, OSError):
            return {"status": "ok", "message": "Daemon stopped"}
    finally:
        await client.close()


def start_daemon_detached(workspace: str, timeout_seconds: float = 10.0) -> Dict[str, Any]:
    transport = IPCTransport(workspace)
    existing_pid = transport.read_pid()
    if existing_pid and _pid_alive(existing_pid):
        try:
            if asyncio.run(_probe_daemon(workspace)):
                return {
                    "status": "ok",
                    "message": f"Daemon is already running (PID {existing_pid})",
                    "pid": existing_pid,
                }
        except Exception:
            pass
        # A live PID with failed authenticated IPC is ambiguous. Never
        # delete/replace its state and never signal it blindly.
        return {
            "status": "error",
            "error": (
                f"PID {existing_pid} is alive but KITT daemon authentication "
                "failed; refusing to replace or signal it"
            ),
            "pid": existing_pid,
        }
    elif existing_pid:
        transport.cleanup()

    root = str(Path(workspace).resolve())
    # Global options precede the subcommand in argparse. The previous launcher
    # used unsupported ``daemon run --workspace`` and could never start.
    cmd = [
        sys.executable,
        "-m",
        "kitt.cli.main",
        "--root",
        root,
        "daemon",
        "run",
    ]
    kwargs: Dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "cwd": root,
        "env": sanitized_subprocess_env(),
    }
    if os.name != "nt":
        kwargs["start_new_session"] = True
    else:
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )

    proc = subprocess.Popen(cmd, **kwargs)
    deadline = time.monotonic() + max(1.0, float(timeout_seconds))
    while time.monotonic() < deadline:
        try:
            if asyncio.run(_probe_daemon(workspace)):
                pid = transport.read_pid() or proc.pid
                return {
                    "status": "ok",
                    "message": f"Daemon started successfully in background (PID {pid})",
                    "pid": pid,
                }
        except Exception:
            pass
        if proc.poll() is not None:
            break
        time.sleep(0.1)

    _terminate_spawned(proc)
    if not _pid_alive(proc.pid):
        transport.cleanup()
    return {
        "status": "error",
        "error": f"Daemon failed to report readiness within {timeout_seconds}s",
        "pid": proc.pid,
    }


def stop_daemon(workspace: str) -> Dict[str, Any]:
    """Stop only through authenticated IPC; never signal a PID file blindly."""
    transport = IPCTransport(workspace)
    try:
        result = asyncio.run(_stop_daemon_via_ipc(workspace))
        if result.get("status") == "ok":
            return {
                "status": "ok",
                "message": "Daemon stopped via authenticated IPC",
                "response": result.get("response"),
            }
    except Exception:
        pass

    pid = transport.read_pid()
    if pid and _pid_alive(pid):
        return {
            "status": "error",
            "error": (
                f"Daemon PID {pid} is alive but authenticated IPC stop failed; "
                "refusing to signal an unverified process"
            ),
        }
    if pid:
        transport.cleanup()
        return {"status": "ok", "message": "Removed stale daemon state"}
    return {"status": "error", "error": "Daemon is not running"}


def get_daemon_status(workspace: str) -> Dict[str, Any]:
    transport = IPCTransport(workspace)
    try:
        running = asyncio.run(_probe_daemon(workspace))
    except Exception:
        running = False
    pid = transport.read_pid()
    endpoint = transport.read_endpoint_metadata()
    return {
        "running": running,
        "pid": pid,
        "pid_alive": bool(pid and _pid_alive(pid)),
        "transport": endpoint.transport_type if endpoint else ("unix" if os.name != "nt" else "tcp"),
        "address": endpoint.address if endpoint else str(transport.socket_path),
        "port": endpoint.port if endpoint else None,
    }
