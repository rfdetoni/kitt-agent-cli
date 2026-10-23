"""Large ELF inventory remains read-only, bounded and constant-memory."""
from __future__ import annotations

import hashlib
from pathlib import Path
import struct
import tempfile

import pytest

from kitt.extensions.builtin_plugins.reverse_engineering.binary import InspectionError
from kitt.extensions.builtin_plugins.reverse_engineering.large_elf import inspect_large_elf
from kitt.extensions.builtin_plugins.reverse_engineering.server import respond


def test_large_sparse_elf_is_streamed_and_original_is_preserved():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        path = root / "large.elf"
        header = bytearray(64)
        header[0:6] = b"\x7fELF\x02\x01"
        struct.pack_into("<H", header, 18, 62)
        length = 64 * 1024 * 1024 + 1
        with path.open("wb") as handle:
            handle.write(header)
            handle.truncate(length)
        stat_before = path.stat()
        report = inspect_large_elf(root, "large.elf")
        assert report["streamed"] and report["read_only"]
        assert report["format"] == "ELF64"
        assert report["architecture"] == "x86_64"
        assert report["size"] == length
        digest = hashlib.sha256(header)
        remaining = length - len(header)
        block = bytes(1024 * 1024)
        while remaining:
            size = min(len(block), remaining)
            digest.update(block[:size])
            remaining -= size
        assert report["sha256"] == digest.hexdigest()
        assert path.stat().st_size == stat_before.st_size
        assert path.stat().st_mtime_ns == stat_before.st_mtime_ns
        rpc = respond({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, root)
        assert "elf_inspect_stream" in [item["name"] for item in rpc["result"]["tools"]]


def test_large_elf_rejects_traversal_and_symlink():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        with pytest.raises(InspectionError):
            inspect_large_elf(root, "../escape")
        with pytest.raises(InspectionError):
            inspect_large_elf(root, "missing.elf")
        target = root / "not-elf"
        target.write_bytes(bytes(64))
        with pytest.raises(InspectionError):
            inspect_large_elf(root, "not-elf")
        link = root / "shortcut.elf"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            pass
        else:
            with pytest.raises(InspectionError):
                inspect_large_elf(root, "shortcut.elf")
