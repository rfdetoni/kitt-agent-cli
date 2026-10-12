"""Operator-only Windows VM setup from a user-provided ISO or standalone QCOW2.

This module never downloads Windows/Tiny11, changes the supplied image, or
installs guest software. QEMU uses UEFI, private TPM 2.0 state and offline
networking; Windows installation is an explicitly interactive VNC session.
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

from .vm_supervisor import VMError, _terminate_group

DEFAULT_DIR = Path.home() / ".local/share/kitt/vm/windows-oracle"
FIRMWARE_PAIRS = (
    ("/usr/share/OVMF/OVMF_CODE_4M.secboot.fd", "/usr/share/OVMF/OVMF_VARS_4M.ms.fd"),
    ("/usr/share/OVMF/OVMF_CODE_4M.fd", "/usr/share/OVMF/OVMF_VARS_4M.fd"),
    ("/usr/share/OVMF/OVMF_CODE.fd", "/usr/share/OVMF/OVMF_VARS.fd"),
    ("/usr/share/edk2/ovmf/OVMF_CODE.secboot.fd", "/usr/share/edk2/ovmf/OVMF_VARS.fd"),
)
VARIANTS = ("official", "tiny11", "tiny11-core", "custom")
MAX_SOURCE = 32 * 1024 ** 3


def _program(name: str) -> str:
    value = shutil.which(name)
    if not value:
        raise VMError(f"Missing {name}; install QEMU, OVMF and swtpm from your Linux distribution")
    return value


def _path(value: str | Path, *, exists: bool = False) -> Path:
    p = Path(value).expanduser().absolute()
    if any(c in str(p) for c in (",", "\n", "\r", "\x00")):
        raise VMError("Filename contains an unsafe QEMU option delimiter")
    if p.is_symlink():
        raise VMError("Symlink source is not accepted")
    if exists:
        info = p.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size <= 0 or info.st_size > MAX_SOURCE:
            raise VMError("Source file must be a bounded regular file")
    return p


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _ovmf(code: Path | None, variables: Path | None) -> tuple[Path, Path]:
    if (code is None) != (variables is None):
        raise VMError("Both OVMF code and variables template must be supplied together")
    if code is not None:
        return _path(code, exists=True), _path(variables, exists=True)
    for a, b in FIRMWARE_PAIRS:
        if Path(a).is_file() and Path(b).is_file():
            return _path(a, exists=True), _path(b, exists=True)
    raise VMError("UEFI OVMF firmware not found; provide --ovmf-code and --ovmf-vars")


def _qcow2_info(path: Path, qemu_img: str) -> dict:
    result = subprocess.run([qemu_img, "info", "--output=json", str(path)],
                            capture_output=True, timeout=30, check=False)
    if result.returncode or len(result.stdout) > 65536:
        raise VMError("qemu-img could not inspect the user-provided disk")
    try:
        data = json.loads(result.stdout)
    except ValueError as exc:
        raise VMError("Invalid qemu-img disk report") from exc
    if (not isinstance(data, dict) or data.get("format") != "qcow2"
            or data.get("backing-filename") or data.get("backing-filename-format")
            or data.get("data-file") or data.get("data-file-raw") or data.get("encrypted")):
        raise VMError("Only standalone, unencrypted QCOW2 disks are supported")
    return data


def prepare(source: Path, *, kind: str, destination: Path = DEFAULT_DIR,
            variant: str = "custom", memory_mib: int = 4096, cpus: int = 2,
            disk_gib: int = 64, ovmf_code: Path | None = None,
            ovmf_vars: Path | None = None) -> Path:
    """Create private VM assets without modifying the user-provided image."""
    if sys.platform != "linux":
        raise VMError("Windows VM wizard requires a Linux QEMU host")
    if kind not in ("iso", "qcow2") or variant not in VARIANTS:
        raise VMError("Unknown source format or Windows edition")
    if any(type(x) is not int for x in (memory_mib, cpus, disk_gib)):
        raise VMError("CPU/memory/disk limits must be integers")
    if not (4096 <= memory_mib <= 16384 and 2 <= cpus <= 8 and 32 <= disk_gib <= 256):
        raise VMError("VM resources out of bounded range (RAM 4–16 GiB, CPU 2–8, disk 32–256 GiB)")
    src = _path(source, exists=True)
    dest = _path(destination)
    if dest.exists():
        raise VMError("VM destination exists; refusing to overwrite an existing VM")
    parent = dest.parent
    parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    if parent.is_symlink() or (os.name == "posix" and
                               (parent.stat().st_uid != os.getuid() or stat.S_IMODE(parent.stat().st_mode) & 0o077)):
        raise VMError("VM parent directory must be private to current user (0700)")
    if dest == src or src.is_relative_to(dest):
        raise VMError("Guest source may not be stored in the new VM directory")
    qemu_img = _program("qemu-img")
    _program("qemu-system-x86_64")
    _program("swtpm")
    code, variables = _ovmf(ovmf_code, ovmf_vars)
    if kind == "qcow2":
        _qcow2_info(src, qemu_img)
    elif src.suffix.lower() != ".iso":
        raise VMError("ISO source must have .iso suffix")

    with tempfile.TemporaryDirectory(prefix=".kitt-windows-", dir=parent) as tmp:
        stage = Path(tmp)
        disk = stage / "windows.qcow2"
        if kind == "iso":
            subprocess.run([qemu_img, "create", "-f", "qcow2", str(disk), f"{disk_gib}G"],
                           check=True, timeout=30, stdout=subprocess.DEVNULL)
        else:
            shutil.copyfile(src, disk)
            _qcow2_info(disk, qemu_img)
        disk.chmod(0o600)
        vars_disk = stage / "OVMF_VARS.fd"
        shutil.copyfile(variables, vars_disk)
        vars_disk.chmod(0o600)
        (stage / "tpm").mkdir(mode=0o700)
        profile = {
            "schema_version": 1,
            "source_kind": kind,
            "source": str(src),
            "source_sha256": _sha256(src),
            "variant": variant,
            "disk": str(dest / "windows.qcow2"),
            "ovmf_code": str(code),
            "ovmf_vars": str(dest / "OVMF_VARS.fd"),
            "tpm_state": str(dest / "tpm"),
            "memory_mib": memory_mib,
            "cpus": cpus,
        }
        (stage / "profile.json").write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
        (stage / "profile.json").chmod(0o600)
        stage.rename(dest)
    return dest / "profile.json"


def _profile(path: Path) -> dict:
    file = _path(path, exists=True)
    if file.stat().st_size > 16384 or (os.name == "posix" and
            (file.stat().st_uid != os.getuid() or stat.S_IMODE(file.stat().st_mode) & 0o077)):
        raise VMError("Windows VM profile must be private and bounded")
    def unique(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise VMError("Duplicate Windows VM config field")
            obj[key] = value
        return obj
    try:
        info = json.loads(file.read_text("utf-8"), object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(VMError("Nonfinite value")))
    except (ValueError, UnicodeError) as exc:
        raise VMError("Invalid Windows VM profile JSON") from exc
    expected = {"schema_version", "source_kind", "source", "source_sha256", "variant",
                "disk", "ovmf_code", "ovmf_vars", "tpm_state", "memory_mib", "cpus"}
    if not isinstance(info, dict) or set(info) != expected or type(info["schema_version"]) is not int or info["schema_version"] != 1:
        raise VMError("Unknown Windows VM profile schema")
    if (info["source_kind"] not in ("iso", "qcow2") or info["variant"] not in VARIANTS
            or not isinstance(info["source_sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", info["source_sha256"])
            or any(type(info[n]) is not int for n in ("memory_mib", "cpus"))
            or not (4096 <= info["memory_mib"] <= 16384 and 2 <= info["cpus"] <= 8)):
        raise VMError("Invalid VM profile values")
    root = file.parent
    for name in ("disk", "ovmf_vars", "tpm_state"):
        target = _path(info[name])
        if target.parent != root and name != "tpm_state":
            raise VMError("VM files must be inside the private VM directory")
        if name == "tpm_state":
            if target != root / "tpm" or target.is_symlink() or not target.is_dir():
                raise VMError("TPM state directory is invalid")
        else:
            _path(target, exists=True)
    _path(info["ovmf_code"], exists=True)
    return info


def _qemu_args(info: dict, vnc_display: int, install: bool, tpm_socket: Path,
               qemu: str) -> list[str]:
    if type(vnc_display) is not int or not 1 <= vnc_display <= 99:
        raise VMError("VNC display must be 1..99 (localhost port 5901..5999)")
    cmd = [
        qemu, "-name", "kitt-windows-lab", "-nodefaults", "-no-user-config",
        "-machine", "q35,accel=kvm:tcg", "-cpu", "max", "-smp", str(info["cpus"]),
        "-m", str(info["memory_mib"]),
        "-display", "none", "-vnc", f"127.0.0.1:{vnc_display}",
        "-monitor", "none", "-serial", "none",
        "-drive", f"if=pflash,format=raw,readonly=on,file={info['ovmf_code']}",
        "-drive", f"if=pflash,format=raw,file={info['ovmf_vars']}",
        "-drive", f"file={info['disk']},format=qcow2,if=ide,index=0,media=disk",
        "-boot", "order=d" if install else "order=c",
        "-vga", "std", "-device", "qemu-xhci,id=xhci",
        "-device", "usb-kbd,bus=xhci.0", "-device", "usb-tablet,bus=xhci.0",
        "-nic", "none",
        "-chardev", f"socket,id=chrtpm,path={tpm_socket}",
        "-tpmdev", "emulator,id=tpm0,chardev=chrtpm",
        "-device", "tpm-tis,tpmdev=tpm0",
    ]
    if install:
        cmd.extend(["-drive", f"file={info['source']},format=raw,if=ide,index=1,media=cdrom,readonly=on"])
    return cmd


def boot(profile_path: Path, *, install: bool = False, vnc_display: int = 1) -> None:
    """Boot using host-restricted VNC and persistent UEFI/TPM state."""
    if sys.platform != "linux":
        raise VMError("Windows guest requires Linux")
    info = _profile(profile_path)
    if install:
        if info["source_kind"] != "iso":
            raise VMError("Install mode requires user-supplied ISO, not an existing QCOW2")
        if _sha256(_path(info["source"], exists=True)) != info["source_sha256"]:
            raise VMError("Source ISO changed since VM setup")
    qemu, swtpm = _program("qemu-system-x86_64"), _program("swtpm")
    if type(vnc_display) is not int or not 1 <= vnc_display <= 99:
        raise VMError("VNC display must be 1..99")
    root = Path(info["disk"]).parent
    with tempfile.TemporaryDirectory(prefix=".kitt-tpm-", dir=root) as tmp:
        socket_path = Path(tmp) / "s"
        argv = [
            swtpm, "socket", "--tpm2", "--tpmstate", f"dir={info['tpm_state']}",
            "--ctrl", f"type=unixio,path={socket_path}", "--flags", "startup-clear",
        ]
        with open(os.devnull, "wb") as sink:
            tpm = subprocess.Popen(argv, stdout=sink, stderr=sink, start_new_session=True)
            try:
                for _ in range(100):
                    if tpm.poll() is not None:
                        raise VMError("TPM emulator exited unexpectedly")
                    if socket_path.exists():
                        break
                    time.sleep(0.05)
                else:
                    raise VMError("TPM socket was not created")
                cmd = _qemu_args(info, vnc_display, install, socket_path, qemu)
                print(f"Windows VM VNC: localhost:{5900 + vnc_display}", flush=True)
                print("The installation is interactive; the ISO is never modified.", flush=True)
                proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, start_new_session=True)
                try:
                    result = proc.wait()
                    if result != 0:
                        raise VMError(f"QEMU exited with status {result}")
                finally:
                    _terminate_group(proc)
            finally:
                _terminate_group(tpm)


def _ask_positive(prompt: str, current: int) -> int:
    raw = input(f"{prompt} [{current}]: ").strip()
    if not raw:
        return current
    if not raw.isdecimal():
        raise VMError("Resources must be positive integers")
    return int(raw)


def wizard(*, source: Path | None = None, kind: str | None = None,
           destination: Path | None = None, variant: str | None = None,
           memory_mib: int = 4096, cpus: int = 2, disk_gib: int = 64,
           ovmf_code: Path | None = None, ovmf_vars: Path | None = None,
           yes: bool = False) -> Path:
    """Interactive once; fully scriptable via --yes and explicit --source."""
    if source is None:
        if yes or not sys.stdin.isatty():
            raise VMError("Noninteractive wizard requires --source")
        source = Path(input("Caminho da sua ISO Windows ou disco QCOW2: ").strip())
    if kind is None:
        kind = "qcow2" if source.suffix.lower() == ".qcow2" else "iso"
    if not yes:
        if not sys.stdin.isatty():
            raise VMError("Use --yes in noninteractive mode")
        if variant is None:
            selected = input("Tipo da imagem (official/tiny11/tiny11-core/custom) [custom]: ").strip()
            variant = selected or "custom"
        memory_mib = _ask_positive("RAM (MiB)", memory_mib)
        cpus = _ask_positive("Núcleos virtuais", cpus)
        if kind == "iso":
            disk_gib = _ask_positive("Disco (GiB)", disk_gib)
        if destination is None:
            selected = input(f"Diretório da VM [{DEFAULT_DIR}]: ").strip()
            destination = Path(selected).expanduser() if selected else DEFAULT_DIR
    if variant is None:
        variant = "custom"
    if destination is None:
        destination = DEFAULT_DIR
    if not yes:
        summary = f"{kind.upper()}={source}; variante={variant}; RAM={memory_mib}MiB; CPU={cpus}; disco={disk_gib}GiB; destino={destination}"
        print(summary)
        if input("Criar VM Windows offline com estes parâmetros? [s/N] ").strip().lower() not in ("s", "sim", "y", "yes"):
            raise VMError("VM creation cancelled")
    return prepare(source, kind=kind, destination=destination, variant=variant,
                   memory_mib=memory_mib, cpus=cpus, disk_gib=disk_gib,
                   ovmf_code=ovmf_code, ovmf_vars=ovmf_vars)
