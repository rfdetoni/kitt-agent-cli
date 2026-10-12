"""One-time Debian 13 minimal guest bootstrap for the host-owned QEMU supervisor.

The host verifies official cloud image checksums, injects a *fresh* restricted
SSH key with NoCloud, and seals the resulting qcow2 only after the guest's
KITT worker responds to a read-only health request. Provisioning alone may
use QEMU NAT for apt/PyPI/GitHub downloads; normal missions remain restricted.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request

from .vm_supervisor import VMError, _check_image, _forward_port, _profile, _ssh_args, _terminate_group

BASE = "https://cloud.debian.org/images/cloud/trixie/latest/"
MIRROR = "https://cloudfront.debian.net/cdimage/cloud/trixie/latest/"
IMAGE = "debian-13-genericcloud-amd64.qcow2"
MAX_IMAGE_BYTES = 2 * 1024 ** 3
MAX_SUMS_BYTES = 128 * 1024
UV_VERSION = "0.12.21"
_ALLOWED_HEX = re.compile(r"^[0-9a-f]{128}$")


def _program(name: str) -> str:
    executable = shutil.which(name)
    if not executable:
        raise VMError(f"Missing {name}; install QEMU, cloud-image-utils and OpenSSH first")
    return executable


def _read_https(url: str, max_bytes: int) -> bytes:
    # Fixed official URLs; no user-supplied network destinations.
    if not url.startswith(BASE):
        raise VMError("Only the official Debian Cloud image directory is accepted")
    with urllib.request.urlopen(url, timeout=40) as response:
        if not (response.url.startswith(BASE) or response.url.startswith(MIRROR)) or response.status != 200:
            raise VMError("Debian download redirected outside the official mirror")
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise VMError("Debian download exceeds size limit")
    return data


def expected_sha512() -> str:
    text = _read_https(BASE + "SHA512SUMS", MAX_SUMS_BYTES).decode("utf-8")
    matches = []
    for line in text.splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) == 2 and parts[1].lstrip("*").removeprefix("./") == IMAGE:
            matches.append(parts[0].lower())
    if len(matches) != 1 or not _ALLOWED_HEX.fullmatch(matches[0]):
        raise VMError("Official Debian SHA512SUMS does not uniquely identify the cloud image")
    return matches[0]


def download_debian(destination: Path, expected: str) -> None:
    if not _ALLOWED_HEX.fullmatch(expected):
        raise VMError("Invalid official Debian SHA512")
    digest = hashlib.sha512()
    total = 0
    request = urllib.request.Request(BASE + IMAGE, headers={"User-Agent": "KITT-VM-Bootstrap/1"})
    with urllib.request.urlopen(request, timeout=90) as response:
        if response.status != 200 or not response.url.startswith(BASE):
            raise VMError("Unexpected Debian cloud image endpoint")
        with destination.open("xb") as stream:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_IMAGE_BYTES:
                    raise VMError("Debian cloud image exceeds size limit")
                stream.write(chunk)
                digest.update(chunk)
    if digest.hexdigest() != expected:
        raise VMError("Debian cloud image does not match published SHA512")


def _bootstrap_script(agent_sha: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{40}", agent_sha):
        raise VMError("Agent source must use immutable Git SHA")
    return (
        "#!/bin/sh\n"
        "set -eu\n"
        "export DEBIAN_FRONTEND=noninteractive\n"
        "python3 -m venv /opt/kitt-bootstrap\n"
        f"/opt/kitt-bootstrap/bin/pip install --no-cache-dir uv=={UV_VERSION}\n"
        "export UV_PYTHON_INSTALL_DIR=/opt/kitt-python\n"
        "/opt/kitt-bootstrap/bin/uv python install 3.14\n"
        "/opt/kitt-bootstrap/bin/uv venv --python 3.14 /opt/kitt/venv\n"
        f"/opt/kitt-bootstrap/bin/uv pip install --python /opt/kitt/venv/bin/python "
        f"'git+https://github.com/rfdetoni/kitt-agent-cli.git@{agent_sha}'\n"
        "/opt/kitt/venv/bin/python -c 'import sys, kitt; "
        "assert sys.version_info >= (3,14); print(kitt.__version__)'\n"
        "install -d -m 0700 -o kitt -g kitt /home/kitt/workspace\n"
        "touch /var/lib/kitt-bootstrap-ok\n"
    )


def cloud_config(public_key: str, agent_sha: str) -> str:
    public_key = public_key.strip()
    fields = public_key.split()
    if len(fields) < 2 or fields[0] != "ssh-ed25519" or not re.fullmatch(r"[A-Za-z0-9+/=]+", fields[1]):
        raise VMError("Bootstrap requires an Ed25519 SSH public key")
    forced = 'restrict,command="/opt/kitt/venv/bin/python -m kitt.vm_worker --workspace /home/kitt/workspace" '
    user = {
        "name": "kitt", "shell": "/bin/bash", "lock_passwd": True,
        "sudo": "ALL=(ALL) NOPASSWD:ALL", "groups": ["sudo"],
        "ssh_authorized_keys": [forced + " ".join(fields[:2])],
    }
    value = {
        "hostname": "kitt-agent",
        "manage_etc_hosts": True, "ssh_pwauth": False, "disable_root": True,
        "users": [user],
        "package_update": True,
        "packages": ["openssh-server", "sudo", "git", "ca-certificates",
                     "python3-venv", "python3-pip", "build-essential", "pkg-config"],
        "write_files": [{"path": "/usr/local/sbin/kitt-bootstrap",
                         "permissions": "0755", "owner": "root:root",
                         "content": _bootstrap_script(agent_sha)}],
        "runcmd": [["/usr/local/sbin/kitt-bootstrap"]],
    }
    # A JSON document is valid YAML, as understood by the cloud-init parser.
    return "#cloud-config\n" + json.dumps(value, indent=2) + "\n"


def _ssh_identity(folder: Path, ssh_keygen: str) -> tuple[Path, str]:
    key = folder / "identity"
    subprocess.run([ssh_keygen, "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
                   check=True, timeout=20, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    key.chmod(0o600)
    return key, key.with_suffix(".pub").read_text(encoding="ascii").strip()


def _known_host(port: int, scan: str) -> str | None:
    result = subprocess.run([scan, "-T", "2", "-p", str(port), "-t", "ed25519", "127.0.0.1"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5, check=False)
    lines = [line.split() for line in result.stdout.decode("ascii", "replace").splitlines()
             if line and not line.startswith("#")]
    keys = {line[2] for line in lines if len(line) == 3 and line[1] == "ssh-ed25519"}
    if len(keys) != 1:
        return None
    return "kitt-sandbox ssh-ed25519 " + next(iter(keys)) + "\n"


def _health(key: Path, known: Path, port: int, ssh: str, version: str) -> bool:
    info = {"ssh_key": str(key), "known_hosts": str(known),
            "host_key_alias": "kitt-sandbox", "ssh_user": "kitt"}
    argv = _ssh_args(info, port, ssh)
    try:
        result = subprocess.run(argv, input=b'{"version":1,"method":"vm.health"}\n',
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=10, check=False)
        if result.returncode or len(result.stdout) > 4096:
            return False
        response = json.loads(result.stdout)
        return (isinstance(response, dict)
                and response.get("version") == 1
                and response.get("status") == "READY"
                and response.get("agent_version") == version
                and response.get("python_minor") == "3.14")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def _file_sha256(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def prepare_debian(destination: Path, *, gateway_port: int = 3000,
                   timeout_seconds: int = 1200, agent_sha: str | None = None) -> Path:
    """Create an immutable Debian guest and private supervisor profile once.

    Never edits an existing profile or installs privileged packages on host.
    """
    if sys.platform != "linux":
        raise VMError("Debian VM bootstrap requires Linux host")
    if type(gateway_port) is not int or not 0 <= gateway_port <= 65535:
        raise VMError("Gateway port must be an integer between 0 and 65535")
    if type(timeout_seconds) is not int or not 180 <= timeout_seconds <= 3600:
        raise VMError("Bootstrap timeout must be 180..3600 seconds")
    destination = destination.expanduser().absolute()
    if any(c in str(destination) for c in (",", "\n", "\r", "\x00")):
        raise VMError("Unsafe VM storage path for QEMU")
    if destination.is_symlink():
        raise VMError("VM destination may not be a symlink")
    if destination.exists():
        profile = destination / "profile.json"
        if not profile.is_file():
            raise VMError("Existing VM directory is incomplete; refusing to overwrite")
        _check_image(_profile(profile), _program("qemu-img"))
        return profile

    if agent_sha is None:
        git = _program("git")
        resolved = subprocess.run(
            [git, "ls-remote", "https://github.com/rfdetoni/kitt-agent-cli.git",
             "refs/heads/main"], capture_output=True, check=True, timeout=30, text=True,
        )
        agent_sha = resolved.stdout.partition("\t")[0]
    if not isinstance(agent_sha, str) or not re.fullmatch(r"[a-f0-9]{40}", agent_sha):
        raise VMError("Cannot resolve an immutable KITT Agent revision")
    from kitt import __version__ as expected_version

    dependencies = {name: _program(name) for name in
                    ("qemu-system-x86_64", "qemu-img", "cloud-localds",
                     "ssh", "ssh-keygen", "ssh-keyscan")}
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if destination.parent.is_symlink():
        raise VMError("Destination parent must not be a symlink")
    if os.name == "posix" and (
        destination.parent.stat().st_uid != os.getuid()
        or stat.S_IMODE(destination.parent.stat().st_mode) & 0o077
    ):
        raise VMError("VM destination parent must be private (0700)")
    expected = expected_sha512()
    with tempfile.TemporaryDirectory(prefix=".kitt-debian-", dir=destination.parent) as tmp:
        stage = Path(tmp)
        image = stage / "debian.qcow2"
        download_debian(image, expected)
        check = subprocess.run([dependencies["qemu-img"], "info", "--output=json", str(image)],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               timeout=25, check=True)
        metadata = json.loads(check.stdout)
        if (metadata.get("format") != "qcow2" or metadata.get("backing-filename")
                or metadata.get("data-file") or metadata.get("encrypted")):
            raise VMError("Unsafe Debian base image")
        subprocess.run([dependencies["qemu-img"], "resize", "-f", "qcow2", str(image), "12G"],
                       check=True, timeout=25, stdout=subprocess.DEVNULL)
        image.chmod(0o600)
        key, public = _ssh_identity(stage, dependencies["ssh-keygen"])
        (stage / "user-data").write_text(cloud_config(public, agent_sha), encoding="utf-8")
        (stage / "meta-data").write_text("instance-id: kitt-debian-seed\nlocal-hostname: kitt-agent\n", encoding="utf-8")
        seed = stage / "seed.iso"
        subprocess.run([dependencies["cloud-localds"], str(seed), str(stage / "user-data"),
                        str(stage / "meta-data")], timeout=25, check=True,
                       stdout=subprocess.DEVNULL)
        port = _forward_port()
        argv = [
            dependencies["qemu-system-x86_64"], "-nodefaults", "-no-user-config",
            "-no-reboot", "-display", "none", "-monitor", "none", "-serial", "none",
            "-machine", "q35,accel=kvm:tcg", "-m", "2048", "-smp", "2",
            "-drive", f"file={image},format=qcow2,if=virtio",
            "-drive", f"file={seed},format=raw,if=virtio,readonly=on",
            "-netdev", f"user,id=provision,ipv6=off,hostfwd=tcp:127.0.0.1:{port}-:22",
            "-device", "virtio-net-pci,netdev=provision",
        ]
        with (stage / "qemu-bootstrap.log").open("wb") as log:
            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            try:
                deadline = time.monotonic() + timeout_seconds
                known = stage / "known_hosts"
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise VMError("Debian bootstrap VM exited unexpectedly")
                    if not known.exists():
                        line = _known_host(port, dependencies["ssh-keyscan"])
                        if line:
                            known.write_text(line, encoding="ascii")
                            known.chmod(0o600)
                    if known.exists() and _health(key, known, port, dependencies["ssh"], expected_version):
                        break
                    time.sleep(3)
                else:
                    raise VMError("Debian bootstrap failed or timed out; image not promoted")
            finally:
                _terminate_group(process)
        # Never preserve cloud-init seed/credentials other than the private SSH identity.
        for name in ("user-data", "meta-data", "seed.iso", "qemu-bootstrap.log"):
            (stage / name).unlink(missing_ok=True)
        profile = {
            "version": 1, "image": str(destination / image.name),
            "sha256": _file_sha256(image),
            "ssh_key": str(destination / key.name),
            "known_hosts": str(destination / "known_hosts"),
            "ssh_user": "kitt", "host_key_alias": "kitt-sandbox",
            "memory_mib": 2048, "cpus": 2, "timeout_seconds": 1200,
            "boot_seconds": 180, "gateway_port": gateway_port,
        }
        (stage / "profile.json").write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
        (stage / "profile.json").chmod(0o600)
        _check_image({**profile, "image": str(image)}, dependencies["qemu-img"])
        stage.rename(destination)
        return destination / "profile.json"
