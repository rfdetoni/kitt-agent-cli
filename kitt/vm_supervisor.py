"""Host-owned, bounded QEMU VM supervisor for disposable autonomous KITT jobs.

This is an opt-in Linux host CLI, NOT an MCP/model-callable tool. Every guest
starts from an independently pinned, unbacked qcow2 image and uses QEMU snapshot
overlay. The only guest network routes are explicit QEMU forwards.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

_NAME = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_SHA = re.compile(r"^[a-f0-9]{64}$")
MAX_CONFIG = 16 * 1024
MAX_REQUEST = 16 * 1024
MAX_RESPONSE = 1024 * 1024
MAX_LOG = 128 * 1024


class VMError(ValueError):
    """Refuse to proceed if isolation preconditions cannot be established."""


def _regular(path: Path, *, private: bool = False) -> Path:
    path = path.expanduser().absolute()
    if path.is_symlink():
        raise VMError("Symlink path refused")
    try:
        info = path.stat()
    except OSError as error:
        raise VMError("Required file is unavailable") from error
    if not stat.S_ISREG(info.st_mode):
        raise VMError("Expected a regular file")
    if os.name == "posix":
        if info.st_mode & stat.S_IWOTH:
            raise VMError("World-writable input refused")
        if private and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
            raise VMError("Private file must be user-owned, mode 0600")
    return path


def _profile(file: Path) -> dict:
    file = _regular(file, private=True)
    if file.stat().st_size > MAX_CONFIG:
        raise VMError("VM profile too large")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise VMError("Duplicate configuration field")
            result[key] = value
        return result
    try:
        info = json.loads(
            file.read_text(encoding="utf-8"),
            object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(VMError("Nonfinite number")),
        )
    except (UnicodeError, ValueError, RecursionError) as error:
        raise VMError("Malformed VM profile") from error
    required = {"version", "image", "sha256", "ssh_key", "known_hosts", "ssh_user", "host_key_alias",
                "memory_mib", "cpus", "timeout_seconds", "boot_seconds", "gateway_port"}
    if not isinstance(info, dict) or set(info) != required or type(info["version"]) is not int or info["version"] != 1:
        raise VMError("Unknown VM profile schema")
    if not isinstance(info["sha256"], str) or not _SHA.fullmatch(info["sha256"]):
        raise VMError("Image must be SHA-256-pinned")
    if any(type(info[k]) is not int for k in ("memory_mib", "cpus", "timeout_seconds", "boot_seconds", "gateway_port")):
        raise VMError("VM resource limits must be integers")
    if not (512 <= info["memory_mib"] <= 8192 and 1 <= info["cpus"] <= 4 and
            30 <= info["timeout_seconds"] <= 3600 and 5 <= info["boot_seconds"] <= 240 and
            0 <= info["gateway_port"] <= 65535):
        raise VMError("VM resource limit out of range")
    if not all(isinstance(info[k], str) and _NAME.fullmatch(info[k])
               for k in ("ssh_user", "host_key_alias")):
        raise VMError("Invalid SSH user or host key alias")
    for key in ("image", "ssh_key", "known_hosts"):
        if not isinstance(info[key], str) or not Path(info[key]).is_absolute():
            raise VMError("VM file paths must be absolute")
    image = _regular(Path(info["image"]))
    if image.stat().st_size > 32 * 1024 ** 3:
        raise VMError("Base image too large")
    _regular(Path(info["ssh_key"]), private=True)
    _regular(Path(info["known_hosts"]), private=True)
    return info


def _check_image(info: dict, qemu_img: str) -> None:
    image = Path(info["image"])
    with image.open("rb") as source:
        h = hashlib.file_digest(source, "sha256").hexdigest()
    if h != info["sha256"]:
        raise VMError("VM image digest mismatch")
    result = subprocess.run(
        [qemu_img, "info", "--output=json", str(image)],
        capture_output=True, timeout=20, check=False,
    )
    if result.returncode or len(result.stdout) > 64 * 1024:
        raise VMError("qemu-img failed to inspect image")
    try:
        metadata = json.loads(result.stdout)
    except (ValueError, UnicodeError) as error:
        raise VMError("Invalid qemu-img response") from error
    if (metadata.get("format") != "qcow2" or metadata.get("backing-filename")
            or metadata.get("backing-filename-format") or metadata.get("encrypted", False)
            or metadata.get("data-file") or metadata.get("data-file-raw")):
        raise VMError("Only unencrypted, standalone qcow2 images are accepted")


def _ssh_args(info: dict, local_port: int, ssh: str) -> list[str]:
    return [
        ssh, "-T", "-i", info["ssh_key"], "-p", str(local_port),
        "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
        "-o", "IdentitiesOnly=yes", "-o", "ClearAllForwardings=yes",
        "-o", "ConnectionAttempts=1", "-o", "ConnectTimeout=4",
        "-o", "NumberOfPasswordPrompts=0",
        "-o", f"UserKnownHostsFile={info['known_hosts']}",
        "-o", f"HostKeyAlias={info['host_key_alias']}",
        f"{info['ssh_user']}@127.0.0.1",
    ]


def _forward_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


def _vm_args(info: dict, qemu: str, local_port: int, identifier: str) -> list[str]:
    network = (
        "user,id=kittnet,restrict=on,ipv6=off,"
        f"hostfwd=tcp:127.0.0.1:{local_port}-:22"
    )
    if info["gateway_port"]:
        # Only preapproved local LLM gateway exposed as 10.0.2.100:3000.
        network += (
            ",guestfwd=tcp:10.0.2.100:3000-"
            f"tcp:127.0.0.1:{info['gateway_port']}"
        )
    return [
        qemu, "-name", f"kitt-{identifier}", "-nodefaults",
        "-no-user-config", "-no-reboot", "-display", "none",
        "-monitor", "none", "-serial", "none",
        "-machine", "q35,accel=kvm:tcg", "-m", str(info["memory_mib"]),
        "-smp", str(info["cpus"]),
        "-drive", f"file={info['image']},format=qcow2,if=virtio,snapshot=on",
        "-netdev", network, "-device", "virtio-net-pci,netdev=kittnet",
    ]


def _terminate_group(process: subprocess.Popen) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()
    process.wait(timeout=10)


def _run_guest(args: list[str], payload: bytes, timeout: int) -> tuple[int, bytes]:
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        process = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=stdout_file, stderr=stderr_file,
            start_new_session=True,
        )
        try:
            assert process.stdin is not None
            process.stdin.write(payload)
            process.stdin.close()
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            raise VMError("Guest job exceeded deadline") from error
        finally:
            _terminate_group(process)
        stdout_file.seek(0, os.SEEK_END)
        size = stdout_file.tell()
        if size > MAX_RESPONSE:
            raise VMError("Guest response exceeds size limit")
        stdout_file.seek(0)
        return code, stdout_file.read(MAX_RESPONSE)


def run_vm_job(profile: Path, goal: str, *, qemu: str | None = None,
               qemu_img: str | None = None, ssh: str | None = None) -> dict:
    """Run goal in operator-provisioned immutable VM. Never a model-callable tool."""
    if sys.platform != "linux":
        raise VMError("VM supervisor requires a Linux host")
    if not isinstance(goal, str) or not goal.strip() or len(goal.encode("utf-8")) > 8192:
        raise VMError("Invalid goal length")
    info = _profile(profile)
    programs = {}
    for name, choice in (("qemu", qemu or "qemu-system-x86_64"),
                         ("qemu_img", qemu_img or "qemu-img"),
                         ("ssh", ssh or "ssh")):
        binary = shutil.which(choice)
        if not binary:
            raise VMError(f"Missing hypervisor dependency: {name}")
        programs[name] = str(_regular(Path(binary)))
    _check_image(info, programs["qemu_img"])
    identifier = uuid4().hex[:12]
    port = _forward_port()
    vm_args = _vm_args(info, programs["qemu"], port, identifier)
    ssh_args = _ssh_args(info, port, programs["ssh"])
    request = json.dumps({"version": 1, "method": "agent.run",
                          "goal": goal}, separators=(",", ":")).encode() + b"\n"
    with tempfile.TemporaryFile() as vm_log:
        vm = subprocess.Popen(vm_args, stdin=subprocess.DEVNULL, stdout=vm_log,
                              stderr=subprocess.STDOUT, start_new_session=True)
        try:
            deadline = time.monotonic() + info["boot_seconds"]
            while time.monotonic() < deadline:
                if vm.poll() is not None:
                    raise VMError("VM exited before SSH became ready")
                # SSH forced-command accepts one JSON request. Do not send probes
                # for readiness, as a probe might accidentally start agent work.
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=1):
                        break
                except OSError:
                    time.sleep(0.5)
            else:
                raise VMError("Guest SSH port unavailable at boot deadline")
            code, raw = _run_guest(ssh_args, request, info["timeout_seconds"])
            if code != 0:
                raise VMError(f"Guest job failed with status {code}")
            try:
                result = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeError) as error:
                raise VMError("Guest did not return a single JSON result") from error
            if (not isinstance(result, dict) or set(result) !=
                {"version", "status", "exit_code", "output_sha256", "output", "truncated"}
                    or type(result["version"]) is not int or result["version"] != 1
                    or result["status"] not in ("PASS", "BLOCKED")
                    or type(result["exit_code"]) is not int
                    or not isinstance(result["output"], str)
                    or type(result["truncated"]) is not bool
                    or result["truncated"]
                    or not isinstance(result["output_sha256"], str)
                    or hashlib.sha256(result["output"].encode("utf-8")).hexdigest() != result["output_sha256"]
                    or (result["status"] == "PASS" and result["exit_code"] != 0)):
                raise VMError("Invalid guest result")
            return {"mission_id": identifier, "guest": result,
                    "artifact_requires_host_review": True}
        finally:
            _terminate_group(vm)
    # The base image is never committed. QEMU snapshot overlay disappears.


def inspect_profile(profile: Path) -> dict:
    """Operator-owned preflight: image digest and tool availability only."""
    if sys.platform != "linux":
        raise VMError("VM supervisor needs Linux host")
    info = _profile(profile)
    for program in ("qemu-system-x86_64", "qemu-img", "ssh"):
        if not shutil.which(program):
            raise VMError(f"Missing prerequisite: {program}")
    _check_image(info, shutil.which("qemu-img"))
    return {"ready": True, "isolation": "qemu-user-restrict-on",
            "guest_image_sha256": info["sha256"],
            "memory_mib": info["memory_mib"], "cpus": info["cpus"],
            "gateway_forward": bool(info["gateway_port"]),
            "guest_provisioned_by_operator": True,
            "live_guest_attestation": False}


def main() -> None:
    parser = argparse.ArgumentParser(description="Operator-only disposable KITT VM execution")
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--goal", required=True)
    args = parser.parse_args()
    try:
        outcome = run_vm_job(args.profile, args.goal)
        print(json.dumps(outcome, ensure_ascii=False))
        raise SystemExit(0 if outcome["guest"]["status"] == "PASS" else 2)
    except (VMError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "BLOCKED", "error": str(error)[:200]}))
        raise SystemExit(2) from error


if __name__ == "__main__":
    main()
