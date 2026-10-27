"""Trusted local Docker broker for autonomous workspace commands.

The agent connects only over a private Unix socket. Only the broker has the
Docker daemon socket. Guest workers receive neither socket nor host secrets.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import socket
import socketserver
import subprocess
import sys
import threading
from typing import Any

from kitt.security.execution_sandbox import ExecutionSandbox, _TOOLCHAIN_IMAGES
from kitt.tools.process_runner import ProcessRunner

MAX_MESSAGE = 131072
MAX_OUTPUT = 262144
SOCKET_ENV = "KITT_SANDBOX_BROKER_SOCKET"
PROFILES = frozenset({"read-only", "workspace-write", "workspace-write+network"})


def _check_command(command: Any) -> list[str]:
    if (not isinstance(command, list) or not command or len(command) > 128
            or sum(len(item) for item in command if isinstance(item, str)) > 65536
            or not all(isinstance(item, str) and item and "\x00" not in item for item in command)):
        raise ValueError("invalid bounded argv")
    return command


def _relative_cwd(value: Any, root: Path) -> str:
    if not isinstance(value, str) or len(value) > 2048 or "\x00" in value:
        raise ValueError("invalid cwd")
    requested = PurePosixPath(value or ".")
    if requested.is_absolute() or ".." in requested.parts:
        raise ValueError("cwd must stay inside workspace")
    resolved = (root / str(requested)).resolve()
    if not resolved.is_relative_to(root) or not resolved.is_dir():
        raise ValueError("cwd is outside workspace or absent")
    return "." if resolved == root else str(resolved.relative_to(root))


def host_workspace_from_mount(docker: str = "docker") -> str:
    """Trust Docker's own bind source for this broker's /workspace mount."""
    name = socket.gethostname()
    fmt = '{{range .Mounts}}{{if eq .Destination "/workspace"}}{{.Source}}{{end}}{{end}}'
    try:
        report = subprocess.run(
            [docker, "inspect", "--format", fmt, name],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("cannot inspect broker workspace bind") from exc
    if report.returncode != 0:
        raise ValueError("cannot discover broker workspace bind")
    source = report.stdout.decode("utf-8", "replace").strip()
    if not source or not Path(source).is_absolute() or source == "/":
        raise ValueError("broker workspace bind is missing or unsafe")
    return source


class DockerBroker:
    """Persistent, bounded Docker workers keyed by workspace/image/authority."""

    def __init__(self, host_workspace: str, docker: str = "docker", *, broker_root: str = "/workspace"):
        selected = host_workspace or host_workspace_from_mount(docker)
        if not selected or not Path(selected).is_absolute() or selected == "/":
            raise ValueError("broker host workspace must be an absolute, non-root path")
        if any(char in selected for char in (",", "\\n", "\\r")):
            raise ValueError("broker workspace source has unsafe Docker mount characters")
        self.host_workspace = selected
        self.root = Path(broker_root).resolve()
        if not self.root.is_dir():
            raise ValueError("broker requires workspace mounted at /workspace")
        self.docker = docker
        self.base_image = os.getenv(
            "KITT_BROKER_DEFAULT_IMAGE", "ghcr.io/rfdetoni/kitt-agent-cli:latest"
        )
        self.allowed_images = frozenset({self.base_image, *_TOOLCHAIN_IMAGES.values()})
        self.runner = ProcessRunner(str(self.root), max_output_bytes=MAX_OUTPUT)
        self.allow_network = os.getenv("KITT_BROKER_ALLOW_NETWORK", "").lower() == "true"
        self._lock_guard = threading.Lock()
        self._worker_locks: dict[str, threading.Lock] = {}

    def _call(self, command: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
        return subprocess.run(
            [self.docker, *command], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout, check=False,
        )

    def _worker_name(self, image: str, profile: str) -> str:
        value = f"{self.host_workspace}\x00{image}\x00{profile}"
        return "kitt-autopilot-" + hashlib.sha256(value.encode()).hexdigest()[:24]

    def _worker(self, image: str, profile: str) -> str:
        if image not in self.allowed_images or profile not in PROFILES:
            raise PermissionError("unapproved worker image or sandbox profile")
        if profile == "workspace-write+network" and not self.allow_network:
            raise PermissionError("network not delegated to the broker by operator")
        name = self._worker_name(image, profile)
        inspected = self._call(["image", "inspect", image, "--format", "{{.Id}}"])
        if inspected.returncode != 0:
            if image not in _TOOLCHAIN_IMAGES.values():
                raise RuntimeError("operator must pre-pull the base broker image")
            pulled = self._call(["pull", image], timeout=600)
            if pulled.returncode != 0:
                raise RuntimeError("failed to prepare fixed toolchain image")
            inspected = self._call(["image", "inspect", image, "--format", "{{.Id}}"])
        if inspected.returncode != 0 or not inspected.stdout.strip():
            raise RuntimeError("unable to inspect approved Docker image")
        image_id = inspected.stdout.strip()
        probe = self._call(
            ["inspect", "--format", "{{index .Config.Labels \\"dev.kitt.autopilot\\"}}", name]
        )
        if probe.returncode == 0:
            if probe.stdout.strip() != b"true":
                raise PermissionError("worker name collision without KITT authority label")
            state = self._call(
                ["inspect", "--format",
                 "{{.State.Running}}|{{.Image}}|{{.HostConfig.NetworkMode}}|{{.HostConfig.ReadonlyRootfs}}", name]
            )
            network = "bridge" if profile == "workspace-write+network" else "none"
            expected = b"true|" + image_id + b"|" + network.encode() + b"|true"
            if state.returncode == 0 and state.stdout.strip() == expected:
                return name
            removed = self._call(["rm", "-f", name])
            if removed.returncode != 0:
                raise RuntimeError("stale sandbox worker could not be removed")
        st = self.root.stat()
        command = [
            "run", "-d", "--rm", "--init", "--name", name,
            "--label", "dev.kitt.autopilot=true",
            "--pull=never", "--read-only", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--pids-limit=256",
            "--memory=4g", "--cpus=2",
            "--network", "bridge" if profile == "workspace-write+network" else "none",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=1g",
            "--tmpfs", "/home/kitt:rw,nosuid,nodev,size=1g,mode=1777",
            "--env", "HOME=/home/kitt",
            "--env", "MAVEN_CONFIG=/home/kitt/.m2",
            "--env", "GRADLE_USER_HOME=/home/kitt/.gradle",
            "--env", "CARGO_HOME=/home/kitt/.cargo",
            "--user", f"{st.st_uid}:{st.st_gid}",
            "--mount", (
                f"type=bind,src={self.host_workspace},dst=/workspace"
                + (",readonly" if profile == "read-only" else "")
            ),
            "--workdir", "/workspace",
        ]
        if (self.root / ".kitt").is_symlink() or (self.root / ".kitt").is_file():
            raise PermissionError("KITT control-plane path cannot be a symlink or file")
        # Always mask KITT control-plane data, including when it was absent
        # when this container was first created.
        command.extend(["--mount", "type=tmpfs,dst=/workspace/.kitt"])
        command.extend(["--entrypoint", "/usr/bin/env", image, "sleep", "infinity"])
        launched = self._call(command, timeout=90)
        if launched.returncode != 0:
            raise RuntimeError("unable to start sandbox worker: " +
                               launched.stderr.decode(errors="replace")[-350:])
        return name

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        profile = request.get("profile")
        if profile not in PROFILES:
            raise PermissionError("sandbox profile not delegated")
        argv = _check_command(request.get("argv"))
        cwd = _relative_cwd(request.get("cwd", "."), self.root)
        raw_timeout = request.get("timeout", 120)
        if type(raw_timeout) is not int or not 1 <= raw_timeout <= 3600:
            raise ValueError("invalid command timeout")
        image = ExecutionSandbox._select_toolchain_image(
            self.base_image, argv, self.root / cwd
        )
        name = self._worker_name(image, profile)
        with self._lock_guard:
            worker_lock = self._worker_locks.setdefault(name, threading.Lock())
        with worker_lock:
            name = self._worker(image, profile)
            # The model supplies argv and relative cwd, never Docker arguments.
            command = [
                self.docker, "exec", "-w", str(PurePosixPath("/workspace") / cwd),
                name, *argv,
            ]
            result = self.runner.run(
                command, cwd=self.root, timeout_seconds=raw_timeout,
                sandbox_profile="full-access",
            )
            if result.timed_out:
                # Kill the worker as well: Docker exec can outlive its CLI process.
                self._call(["rm", "-f", name], timeout=15)
        return {
            "returncode": result.returncode if not result.timed_out else 124,
            "stdout": result.stdout[:MAX_OUTPUT],
            "stderr": result.stderr[:MAX_OUTPUT],
        }


class BrokerHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        line = self.rfile.readline(MAX_MESSAGE + 1)
        try:
            if not line or len(line) > MAX_MESSAGE or not line.endswith(b"\n"):
                raise ValueError("request too large or incomplete")
            data = json.loads(line)
            if not isinstance(data, dict):
                raise ValueError("request must be object")
            if data.get("op") == "ping":
                answer = {"returncode": 0, "stdout": "pong", "stderr": ""}
            elif data.get("op") == "exec":
                answer = self.server.broker.execute(data)
            else:
                raise ValueError("unrecognized broker action")
        except Exception as exc:
            answer = {"returncode": 125, "stdout": "", "stderr": str(exc)[:500]}
        self.wfile.write(json.dumps(answer).encode("utf-8") + b"\n")


class BrokerServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, sock_path: str, broker: DockerBroker):
        self.broker = broker
        super().__init__(sock_path, BrokerHandler)


def send(socket_path: str, payload: dict[str, Any], timeout: float = 5) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(socket_path)
        sock.sendall(json.dumps(payload).encode("utf-8") + b"\n")
        data = bytearray()
        while not data.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk or len(data) + len(chunk) > 2 * MAX_OUTPUT + 8192:
                raise ConnectionError("broker response incomplete or too large")
            data.extend(chunk)
    reply = json.loads(data)
    if not isinstance(reply, dict):
        raise ValueError("invalid broker reply")
    return reply


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("serve", "ping", "client"))
    parser.add_argument("payload", nargs="?")
    options = parser.parse_args()
    socket_path = os.getenv(SOCKET_ENV, "/run/kitt-broker/exec.sock")
    if options.command == "serve":
        path = Path(socket_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()
        broker = DockerBroker(os.getenv("KITT_BROKER_HOST_WORKSPACE", ""))
        with BrokerServer(socket_path, broker) as server:
            os.chmod(socket_path, 0o666)
            server.serve_forever()
        return 0
    if options.command == "ping":
        response = send(socket_path, {"op": "ping"})
        return 0 if response.get("stdout") == "pong" else 1
    if not options.payload or len(options.payload) > MAX_MESSAGE:
        return 125
    payload = json.loads(options.payload)
    payload["op"] = "exec"
    response = send(socket_path, payload, timeout=float(payload.get("timeout", 120)) + 15)
    sys.stdout.write(str(response.get("stdout", "")))
    sys.stderr.write(str(response.get("stderr", "")))
    return int(response.get("returncode", 125))


if __name__ == "__main__":
    raise SystemExit(main())
