"""Operator-only Linux reverse-engineering VM wizard.

User supplies ISO/standalone QCOW2. Setup is offline by default, no guest
tooling is downloaded or silently executed on the host. Generated setup scripts
are instructions for use *inside* a deliberately provisioned guest.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile

from .vm_supervisor import VMError, _terminate_group
from .vm_windows import _path, _program, _sha256, _qcow2_info

DEFAULT_DIR = Path.home() / ".local/share/kitt/vm/linux-re"
DISTROS = ("debian", "ubuntu", "kali", "fedora", "arch", "other")
TOOL_PROFILES = ("minimal", "re", "wine", "full")
MAX_PROFILE = 16 * 1024

# Only baseline distro packages with a standard distribution packaging route.
# Ghidra and Frida often need version-specific installers; don't pretend the
# packages are universally available through apt/dnf/pacman.
APT_PACKAGES = ("git", "build-essential", "gdb", "binutils", "file", "strace",
                "cmake", "ninja-build", "pkg-config", "python3", "python3-venv",
                "python3-pip", "default-jdk", "curl")
FEDORA_PACKAGES = ("git", "gcc", "gcc-c++", "make", "gdb", "binutils", "file",
                   "strace", "cmake", "ninja-build", "pkgconf-pkg-config",
                   "python3", "python3-pip", "java-21-openjdk-devel", "curl")
ARCH_PACKAGES = ("git", "base-devel", "gdb", "binutils", "file", "strace",
                 "cmake", "ninja", "pkgconf", "python", "python-pip", "jdk-openjdk", "curl")


def bootstrap_script(distro: str, profile: str) -> str:
    """A human-reviewable guest script, never executed by the host wizard."""
    if distro not in DISTROS or profile not in TOOL_PROFILES:
        raise VMError("Unsupported distribution or reverse-engineering profile")
    header = (
        "#!/bin/sh\n"
        "set -eu\n"
        "# Run ONLY inside your isolated guest, after installing Linux.\n"
        "# Review package sources and allow guest network access only during setup.\n"
    )
    if distro == "other":
        return header + "echo 'Configure packages manually for this distro.'\n"
    if distro in ("debian", "ubuntu", "kali"):
        packages = list(APT_PACKAGES)
        if profile in ("wine", "full"):
            packages.append("wine64")
        if profile in ("re", "wine", "full"):
            packages.extend(("clang", "lldb"))
        command = "apt-get update\napt-get install -y --no-install-recommends " + " ".join(packages) + "\n"
    elif distro == "fedora":
        packages = list(FEDORA_PACKAGES)
        if profile in ("wine", "full"):
            packages.append("wine")
        if profile in ("re", "wine", "full"):
            packages.extend(("clang", "lldb"))
        command = "dnf -y install " + " ".join(packages) + "\n"
    else:
        packages = list(ARCH_PACKAGES)
        if profile in ("wine", "full"):
            packages.append("wine")
        if profile in ("re", "wine", "full"):
            packages.extend(("clang", "lldb"))
        command = "pacman -Syu --noconfirm " + " ".join(packages) + "\n"
    if profile == "minimal":
        return header + "echo 'Minimal guest profile: install additional tools as needed.'\n"
    return header + "if [ \"$(id -u)\" -ne 0 ]; then echo 'Run as root inside guest' >&2; exit 1; fi\n" + command + (
        "echo 'Basic reverse engineering packages installed.'\n"
        "echo 'Install a pinned compatible Ghidra release and Frida Python tooling separately as needed.'\n"
        "echo 'Do not run unknown binaries before taking a clean guest snapshot.'\n"
    )


def prepare(source: Path, *, kind: str, distro: str = "debian",
            tools: str = "re", destination: Path = DEFAULT_DIR,
            memory_mib: int = 4096, cpus: int = 2, disk_gib: int = 64) -> Path:
    if sys.platform != "linux":
        raise VMError("Linux RE wizard needs a Linux QEMU host")
    if kind not in ("iso", "qcow2") or distro not in DISTROS or tools not in TOOL_PROFILES:
        raise VMError("Unsupported Linux source/distribution/tools profile")
    if any(type(v) is not int for v in (memory_mib, cpus, disk_gib)):
        raise VMError("VM resource values must be integers")
    if not (1024 <= memory_mib <= 16384 and 1 <= cpus <= 8 and 16 <= disk_gib <= 256):
        raise VMError("Linux RE resource limits: 1–16 GiB RAM, 1–8 CPU, 16–256 GiB disk")
    src, dest = _path(source, exists=True), _path(destination)
    if kind == "iso" and src.suffix.lower() != ".iso":
        raise VMError("Installation source must be an ISO file")
    if dest.exists():
        raise VMError("Linux RE VM already exists; refusing to overwrite")
    if src == dest or src.is_relative_to(dest):
        raise VMError("Source must remain outside destination")
    parent = dest.parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if (parent.is_symlink() or os.name == "posix" and
            (parent.stat().st_uid != os.getuid() or stat.S_IMODE(parent.stat().st_mode) & 0o077)):
        raise VMError("VM state directory must be user-owned and private (0700)")
    qemu_img = _program("qemu-img")
    _program("qemu-system-x86_64")
    if kind == "qcow2":
        _qcow2_info(src, qemu_img)

    with tempfile.TemporaryDirectory(prefix=".kitt-linux-re-", dir=parent) as tmp:
        stage = Path(tmp)
        disk = stage / "linux.qcow2"
        if kind == "iso":
            subprocess.run([qemu_img, "create", "-f", "qcow2", str(disk), f"{disk_gib}G"],
                           check=True, timeout=30, stdout=subprocess.DEVNULL)
        else:
            shutil.copyfile(src, disk)
            _qcow2_info(disk, qemu_img)
        disk.chmod(0o600)
        setup = stage / "setup-re-tools.sh"
        setup.write_text(bootstrap_script(distro, tools), encoding="utf-8")
        setup.chmod(0o600)
        profile = {
            "schema_version": 1,
            "source_kind": kind, "source": str(src),
            "source_sha256": _sha256(src),
            "disk": str(dest / "linux.qcow2"),
            "distro": distro, "tools": tools,
            "memory_mib": memory_mib, "cpus": cpus,
        }
        output = stage / "profile.json"
        output.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
        output.chmod(0o600)
        stage.rename(dest)
    return dest / "profile.json"


def _profile(path: Path) -> dict:
    src = _path(path, exists=True)
    if src.stat().st_size > MAX_PROFILE or (os.name == "posix" and
            (src.stat().st_uid != os.getuid() or stat.S_IMODE(src.stat().st_mode) & 0o077)):
        raise VMError("Profile must be bounded, private and user-owned")
    def unique(pairs):
        output = {}
        for key, value in pairs:
            if key in output:
                raise VMError("Duplicate Linux profile field")
            output[key] = value
        return output
    try:
        info = json.loads(src.read_text(encoding="utf-8"), object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(VMError("Nonfinite number")))
    except (ValueError, UnicodeError) as exc:
        raise VMError("Invalid Linux profile JSON") from exc
    required = {"schema_version", "source_kind", "source", "source_sha256",
                "disk", "distro", "tools", "memory_mib", "cpus"}
    if (not isinstance(info, dict) or set(info) != required
            or type(info["schema_version"]) is not int or info["schema_version"] != 1
            or info["source_kind"] not in ("iso", "qcow2")
            or info["distro"] not in DISTROS or info["tools"] not in TOOL_PROFILES
            or not isinstance(info["source_sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", info["source_sha256"])
            or any(type(info[k]) is not int for k in ("memory_mib", "cpus"))
            or not (1024 <= info["memory_mib"] <= 16384 and 1 <= info["cpus"] <= 8)):
        raise VMError("Invalid Linux RE profile schema")
    if _path(info["disk"]) != src.parent / "linux.qcow2":
        raise VMError("Linux guest disk must be under private VM directory")
    _path(info["disk"], exists=True)
    return info


def _qemu_args(info: dict, display: int, *, install: bool, qemu: str, network: bool) -> list[str]:
    if type(display) is not int or not 1 <= display <= 99:
        raise VMError("VNC display 1–99 required")
    if type(network) is not bool:
        raise VMError("Network must be opt-in boolean")
    args = [
        qemu, "-name", "kitt-linux-re", "-nodefaults", "-no-user-config",
        "-machine", "q35,accel=kvm:tcg", "-cpu", "max", "-m", str(info["memory_mib"]),
        "-smp", str(info["cpus"]), "-display", "none", "-vnc", f"127.0.0.1:{display}",
        "-monitor", "none", "-serial", "none", "-vga", "std",
        "-device", "qemu-xhci,id=xhci", "-device", "usb-kbd,bus=xhci.0",
        "-device", "usb-tablet,bus=xhci.0",
        "-drive", f"file={info['disk']},format=qcow2,if=ide,index=0,media=disk",
    ]
    if network:
        args.extend(["-netdev", "user,id=re-labnet,ipv6=off",
                     "-device", "virtio-net-pci,netdev=re-labnet"])
    else:
        args.extend(["-nic", "none"])
    if install:
        args.extend(["-boot", "order=d", "-drive",
                     f"file={info['source']},format=raw,if=ide,index=1,media=cdrom,readonly=on"])
    return args


def boot(profile: Path, *, install: bool = False,
         vnc_display: int = 2, network: bool = False) -> None:
    if sys.platform != "linux":
        raise VMError("Linux RE guest needs Linux host")
    info = _profile(profile)
    if install:
        if info["source_kind"] != "iso":
            raise VMError("ISO installation mode cannot be used with preinstalled QCOW2")
        if _sha256(_path(info["source"], exists=True)) != info["source_sha256"]:
            raise VMError("Linux ISO was changed after wizard preparation")
    qemu = _program("qemu-system-x86_64")
    args = _qemu_args(info, vnc_display, install=install, qemu=qemu, network=network)
    print(f"Linux RE VM VNC: 127.0.0.1:{5900 + vnc_display}", flush=True)
    if network:
        print("WARNING: guest NAT enabled explicitly; disable when running untrusted samples.", flush=True)
    proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, start_new_session=True)
    try:
        code = proc.wait()
        if code:
            raise VMError(f"Linux guest QEMU exited with status {code}")
    finally:
        _terminate_group(proc)


def wizard(*, source: Path | None = None, kind: str | None = None,
           distro: str | None = None, tools: str | None = None,
           destination: Path | None = None, memory_mib: int = 4096,
           cpus: int = 2, disk_gib: int = 64, yes: bool = False) -> Path:
    if source is None:
        if yes or not sys.stdin.isatty():
            raise VMError("Provide --source for noninteractive Linux RE VM preparation")
        source = Path(input("ISO Linux ou QCOW2 fornecido pelo usuário: ").strip()).expanduser()
    if kind is None:
        kind = "qcow2" if source.suffix.lower() == ".qcow2" else "iso"
    if not yes:
        if not sys.stdin.isatty():
            raise VMError("Use --yes for noninteractive VM creation")
        distro = distro or (input("Distro (debian/ubuntu/kali/fedora/arch/other) [debian]: ").strip() or "debian")
        tools = tools or (input("Ferramentas (minimal/re/wine/full) [re]: ").strip() or "re")
        memory_mib = _answer_int("RAM em MiB", memory_mib)
        cpus = _answer_int("vCPUs", cpus)
        if kind == "iso":
            disk_gib = _answer_int("Disco em GiB", disk_gib)
        if destination is None:
            given = input(f"Diretório da VM [{DEFAULT_DIR}]: ").strip()
            destination = Path(given).expanduser() if given else DEFAULT_DIR
        print(f"Guest={distro} perfil={tools}; ISO/QCOW2={source}; RAM={memory_mib}; CPUs={cpus}; destino={destination}")
        if input("Criar VM Linux RE offline? [s/N] ").strip().lower() not in ("s", "sim", "y", "yes"):
            raise VMError("Linux RE VM creation cancelled")
    return prepare(source, kind=kind, distro=distro or "debian", tools=tools or "re",
                   destination=destination or DEFAULT_DIR,
                   memory_mib=memory_mib, cpus=cpus, disk_gib=disk_gib)


def _answer_int(name: str, default: int) -> int:
    answer = input(f"{name} [{default}]: ").strip()
    if not answer:
        return default
    if not answer.isdecimal():
        raise VMError("VM resource must be a positive integer")
    return int(answer)
