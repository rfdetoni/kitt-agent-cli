"""Bounded read-only PE/ELF metadata; no disassembly and no execution."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path, PureWindowsPath
import stat
import struct

MAX_BYTES = 64 * 1024 * 1024
MAX_SECTIONS = 96
PE_MACHINE = {0x14C: "x86", 0x8664: "x86_64", 0xAA64: "arm64"}
ELF_MACHINE = {3: "x86", 62: "x86_64", 183: "arm64"}


class InspectionError(ValueError):
    pass


def _num(data: bytes, fmt: str, at: int) -> int:
    size = struct.calcsize(fmt)
    if at < 0 or at + size > len(data):
        raise InspectionError("Truncated binary header")
    return struct.unpack_from(fmt, data, at)[0]


def _pe(data: bytes) -> dict:
    if len(data) < 64:
        raise InspectionError("Truncated MZ header")
    pe = _num(data, "<I", 0x3C)
    if pe < 0x40 or pe + 24 > len(data) or data[pe:pe + 4] != b"PE\0\0":
        raise InspectionError("Invalid PE header")
    machine = _num(data, "<H", pe + 4)
    count = _num(data, "<H", pe + 6)
    opt_size = _num(data, "<H", pe + 20)
    if count > MAX_SECTIONS:
        raise InspectionError("Excessive PE sections")
    optional = pe + 24
    section_table = optional + opt_size
    if opt_size < 20 or section_table > len(data) or section_table + 40 * count > len(data):
        raise InspectionError("Invalid PE optional header or section table")
    magic = _num(data, "<H", optional)
    if magic not in (0x10B, 0x20B):
        raise InspectionError("Unknown PE magic")
    if (magic == 0x20B and opt_size < 112) or (magic == 0x10B and opt_size < 96):
        raise InspectionError("Truncated PE optional header")
    sections = []
    for i in range(count):
        pos = section_table + i * 40
        name = data[pos:pos + 8].split(b"\0", 1)[0].decode("ascii", "replace")
        sections.append({
            "name": name,
            "virtual_address": _num(data, "<I", pos + 12),
            "virtual_size": _num(data, "<I", pos + 8),
            "raw_size": _num(data, "<I", pos + 16),
        })
    return {"format": "PE32+" if magic == 0x20B else "PE32",
            "architecture": PE_MACHINE.get(machine, f"machine_{machine:04x}"),
            "machine": machine, "entry_point_rva": _num(data, "<I", optional + 16),
            "sections": sections}


def _elf(data: bytes) -> dict:
    if len(data) < 20 or data[:4] != b"\x7fELF":
        raise InspectionError("Invalid ELF header")
    elf_class, byte_order = data[4], data[5]
    if elf_class not in (1, 2) or byte_order not in (1, 2):
        raise InspectionError("Invalid ELF class or encoding")
    minimum = 52 if elf_class == 1 else 64
    if len(data) < minimum:
        raise InspectionError("Truncated ELF header")
    endian = "<" if byte_order == 1 else ">"
    machine = _num(data, endian + "H", 18)
    return {"format": "ELF64" if elf_class == 2 else "ELF32",
            "architecture": ELF_MACHINE.get(machine, f"machine_{machine}"),
            "machine": machine, "endianness": "little" if byte_order == 1 else "big"}


def _read_bounded(root: Path, parts: tuple[str, ...]) -> bytes:
    """Traverse directory descriptors without following symlinks on POSIX."""
    flags = (os.O_RDONLY | getattr(os, "O_BINARY", 0) |
             getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    if os.name == "posix" and os.open in os.supports_dir_fd:
        directory = os.open(root, flags | getattr(os, "O_DIRECTORY", 0))
        try:
            for part in parts[:-1]:
                nxt = os.open(part, flags | getattr(os, "O_DIRECTORY", 0), dir_fd=directory)
                os.close(directory)
                directory = nxt
            fd = os.open(parts[-1], flags, dir_fd=directory)
        finally:
            os.close(directory)
    else:
        target = root.joinpath(*parts)
        cursor = root
        for part in parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise InspectionError("Symlinks are forbidden")
        if not target.resolve().is_relative_to(root.resolve()):
            raise InspectionError("Outside workspace")
        fd = os.open(target, flags)
    try:
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or meta.st_size > MAX_BYTES:
            raise InspectionError("Not a regular file or size exceeds 64 MiB")
        with os.fdopen(fd, "rb", closefd=False) as source:
            raw = source.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise InspectionError("Size exceeds 64 MiB")
        return raw
    finally:
        os.close(fd)


def inspect(root: Path, relative_path: str) -> dict:
    if not isinstance(relative_path, str) or not relative_path or "\0" in relative_path:
        raise InspectionError("Invalid path")
    posix = Path(relative_path)
    win = PureWindowsPath(relative_path)
    if posix.is_absolute() or win.is_absolute() or win.drive or any(p in ("..", "") for p in posix.parts):
        raise InspectionError("Path must be workspace-relative")
    try:
        data = _read_bounded(root.resolve(strict=True), posix.parts)
    except (OSError, RuntimeError) as e:
        raise InspectionError(f"Cannot inspect file: {type(e).__name__}") from e
    metadata = _pe(data) if data.startswith(b"MZ") else _elf(data) if data.startswith(b"\x7fELF") else None
    if metadata is None:
        raise InspectionError("Unsupported file format")
    return {"path": relative_path, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), **metadata}
