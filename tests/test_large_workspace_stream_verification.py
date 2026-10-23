"""Host post-edit verifier must read large artifacts without buffering or bypassing gates."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from kitt.security.workspace_fs import (
    DEFAULT_MAX_FILE_BYTES,
    MAX_STREAM_VERIFY_BYTES,
    WorkspaceFileSystem,
)
from kitt.validation.post_edit import PostEditValidator


def test_oversized_binary_gets_bounded_independent_fingerprint(tmp_path: Path):
    binary = tmp_path / "2.1.308"
    size = DEFAULT_MAX_FILE_BYTES + 8193
    with binary.open("wb") as handle:
        handle.write(b"\x7fELF\x02\x01" + bytes(58))
        handle.truncate(size)
    before = binary.stat()
    digest = hashlib.sha256()
    with binary.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    fs = WorkspaceFileSystem(tmp_path)
    # Host reading into memory is still prohibited at 8 MiB.
    with pytest.raises(ValueError, match="Workspace file exceeds"):
        fs.read("2.1.308")
    fingerprint = fs.hash_regular("2.1.308")
    assert fingerprint.size == size
    assert fingerprint.sha256 == digest.hexdigest()
    report = PostEditValidator(tmp_path).validate_paths(["2.1.308"])
    assert report.ok and report.checked == 1 and report.skipped == 0
    assert report.diagnostics[0].validator == "workspace.sha256-stream"
    assert str(size) in report.diagnostics[0].message
    assert digest.hexdigest() in report.diagnostics[0].message
    after = binary.stat()
    assert (before.st_size, before.st_mtime_ns, before.st_ctime_ns) == (
        after.st_size, after.st_mtime_ns, after.st_ctime_ns
    )


def test_stream_rejects_symlinks_and_oversized_sparse_artifact(tmp_path: Path):
    fs = WorkspaceFileSystem(tmp_path)
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.write_bytes(b"outside")
    link = tmp_path / "linked.bin"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pass
    else:
        with pytest.raises((OSError, ValueError)):
            fs.hash_regular("linked.bin")
        report = PostEditValidator(tmp_path).validate_paths(["linked.bin"])
        assert not report.ok
    finally:
        outside.unlink(missing_ok=True)
    too_large = tmp_path / "too-large.bin"
    with too_large.open("wb") as f:
        f.truncate(MAX_STREAM_VERIFY_BYTES + 1)
    with pytest.raises(ValueError, match="exceeds"):
        fs.hash_regular("too-large.bin")
    report = PostEditValidator(tmp_path).validate_paths(["too-large.bin"])
    assert not report.ok
    assert any(d.validator == "workspace-read" for d in report.diagnostics)


def test_large_source_cannot_bypass_memory_bounded_syntax_gate(tmp_path: Path):
    huge_python = tmp_path / "main.py"
    with huge_python.open("wb") as handle:
        handle.write(b"# bounded\n")
        handle.truncate(DEFAULT_MAX_FILE_BYTES + 1)
    report = PostEditValidator(tmp_path).validate_paths(["main.py"])
    assert not report.ok
    assert any(d.validator == "workspace-read" for d in report.diagnostics)


def test_stream_refuses_protected_and_parent_paths(tmp_path: Path):
    fs = WorkspaceFileSystem(tmp_path)
    with pytest.raises(PermissionError):
        fs.hash_regular(".git/HEAD")
    with pytest.raises(PermissionError):
        fs.hash_regular("../outside.bin")
