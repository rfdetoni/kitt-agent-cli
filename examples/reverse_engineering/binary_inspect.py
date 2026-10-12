"""Bounded, read-only PE/ELF binary metadata inspection (no execution)."""
from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
import struct

MAX_BYTES = 64 * 1024 * 1024
MAX_SECTIONS = 96
MAX_NAME = 32
PE_MACHINE = {0x14c: "x86", 0x8664: "x86_64", 0xaa64: "arm64"}
ELF_MACHINE = {3: "x86", 62: "x86_64", 183: "arm64"}


class BinaryInspectionError(ValueError):
    pass


def _u16(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 2 > len(data):
        raise BinaryInspectionError("Truncated binary header")
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise BinaryInspectionError("Truncated binary header")
    return struct.unpack_from("<I", data, offset)[0]


def _pe(data: bytes) -> dict:
    if len(data) < 0x40:
        raise BinaryInspectionError("Truncated DOS header")
    pe = _u32(data, 0x3C)
    if pe < 0x40 or pe + 24 > len(data) or data[pe:pe + 4] != b"PE\x00\x00":
        raise BinaryInspectionError("Invalid PE signature")
    machine = _u16(data, pe + 4)
    sections = _u16(data, pe + 6)
    opt_size = _u16(data, pe + 20)
    if sections > MAX_SECTIONS:
        raise BinaryInspectionError("Excessive PE section count")
    opt_start = pe + 24
    opt_end = opt_start + opt_size
    section_start = opt_end
    if opt_end > len(data) or section_start + sections * 40 > len(data):
        raise BinaryInspectionError("Truncated PE section table")
    if opt_size < 2:
        raise BinaryInspectionError("Missing optional header")
    magic = _u16(data, opt_start)
    if magic not in (0x10B, 0x20B):
        raise BinaryInspectionError("Unsupported PE optional header")
    names = []
    for i in range(sections):
        off = section_start + i * 40
        name = data[off:off + 8].split(b"\0", 1)[0].decode("ascii", errors="replace")
        names.append(name[:MAX_NAME])
    return {
        "format": "PE32+" if magic == 0x20B else "PE32",
        "architecture": PE_MACHINE.get(machine, f"machine_0x{machine:04x}"),
        "machine": machine,
        "section_count": sections,
        "sections": names,
        "entry_point_rva": _u32(data, opt_start + 16) if opt_size >= 20 else None,
    }


def _elf(data: bytes) -> dict:
    if len(data) < 20 or data[:4] != b"\x7fELF":
        raise BinaryInspectionError("Truncated ELF header")
    cls, endian = data[4], data[5]
    if cls not in (1, 2) or endian not in (1, 2):
        raise BinaryInspectionError("Invalid ELF class or byte order")
    order = "<" if endian == 1 else ">"
    machine = struct.unpack_from(order + "H", data, 18)[0]
    return {
        "format": "ELF64" if cls == 2 else "ELF32",
        "architecture": ELF_MACHINE.get(machine, f"machine_{machine}"),
        "machine": machine,
        "endianness": "little" if endian == 1 else "big",
    }


def inspect_file(workspace: Path, relative_path: str) -> dict:
    if not isinstance(relative_path, str) or not relative_path or "\0" in relative_path:
        raise BinaryInspectionError("Expected nonempty relative path")
    path = Path(relative_path)
    if path.is_absolute() or ".." in path.parts:
        raise BinaryInspectionError("Path must stay inside workspace")
    root = workspace.resolve(strict=True)
    target = root / path
    # Refuse symlinks at every path segment to prevent workspace escapes.
    cursor = root
    for part in path.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise BinaryInspectionError("Symlinks are not allowed")
    try:
        fd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise BinaryInspectionError("Cannot open requested file") from exc
    try:
        stat_result = os.fstat(fd)
        if not stat.S_ISREG(stat_result.st_mode) or stat_result.st_size > MAX_BYTES:
            raise BinaryInspectionError("Not a supported regular file or file exceeds size limit")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(MAX_BYTES + 1)
    finally:
        os.close(fd)
    if len(data) > MAX_BYTES:
        raise BinaryInspectionError("File exceeds size limit")
    if data.startswith(b"MZ"):
        metadata = _pe(data)
    elif data.startswith(b"\x7fELF"):
        metadata = _elf(data)
    else:
        raise BinaryInspectionError("Unsupported binary format")
    return {"path": relative_path, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), **metadata}
