"""Opt-in plan for the ruDevolution Rust example; never execute a bundle here."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path, PureWindowsPath
import re
import stat

from .binary import InspectionError, _read_bounded

MAX_BUNDLE_BYTES = 32 * 1024 * 1024
MAX_EXECUTABLE_BYTES = 128 * 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _relative(root: Path, value: str, *, extension: bool = False) -> Path:
    if not isinstance(value, str) or not value or "\0" in value or "\\" in value:
        raise InspectionError("Expected a nonempty POSIX workspace-relative path")
    path, windows = Path(value), PureWindowsPath(value)
    if path.is_absolute() or windows.drive or windows.is_absolute() or ".." in path.parts:
        raise InspectionError("Outside workspace or invalid relative path")
    if extension and path.suffix.lower() not in {".js", ".mjs", ".cjs"}:
        raise InspectionError("ruDevolution accepts only JavaScript bundles")
    if path == Path("."):
        raise InspectionError("Expected a file or output directory")
    return root.joinpath(*path.parts)


def _operator_executable(path: str, expected_sha256: str) -> tuple[str, str]:
    if not isinstance(path, str) or not Path(path).is_absolute():
        raise InspectionError("Operator must supply an absolute executable path")
    if not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256):
        raise InspectionError("Operator must supply a lowercase SHA256 binary pin")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise InspectionError(f"Cannot open pinned executable: {type(exc).__name__}") from exc
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_EXECUTABLE_BYTES:
            raise InspectionError("Executable must be a regular file <= 128 MiB")
        if os.name == "posix" and not metadata.st_mode & 0o111:
            raise InspectionError("Pinned binary must be executable")
        digest = hashlib.sha256()
        while chunk := os.read(fd, 1024 * 1024):
            digest.update(chunk)
        if digest.hexdigest() != expected_sha256:
            raise InspectionError("ruDevolution executable SHA256 does not match pin")
    finally:
        os.close(fd)
    return path, digest.hexdigest()


def plan(root: Path, path: str, executable: str, binary_sha256: str, output_dir: str) -> dict:
    """Return a host-executable argv plan, without network, mutation or execution.

    The upstream example may create output and invoke Node for syntax checks.
    Therefore the operator must run this only through a host-approved sandbox.
    """
    root = root.resolve(strict=True)
    bundle_path = _relative(root, path, extension=True)
    try:
        data = _read_bounded(root, bundle_path.relative_to(root).parts)
    except (OSError, RuntimeError) as exc:
        raise InspectionError(f"Cannot read JavaScript bundle: {type(exc).__name__}") from exc
    if len(data) > MAX_BUNDLE_BYTES:
        raise InspectionError("JavaScript bundle exceeds 32 MiB")
    destination = _relative(root, output_dir)
    try:
        parent = destination.parent.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise InspectionError("Output parent must already exist") from exc
    if not parent.is_relative_to(root) or destination.exists() or destination.is_symlink():
        raise InspectionError("Output must be a fresh directory under an existing workspace parent")
    executable, executable_sha = _operator_executable(executable, binary_sha256)
    return {
        "kind": "rudevolution_host_execution_plan",
        "source": path,
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "source_bytes": len(data),
        "executable_sha256": executable_sha,
        "cwd": str(root),
        "argv": [executable, str(bundle_path), "--output-dir", str(destination)],
        "executed": False,
        "requires_approved_process_run": True,
        "warning": (
            "The upstream Rust example may launch Node during output validation. "
            "Run only inside a host-authorized, resource-bounded, network-restricted sandbox; "
            "recheck source and executable hashes before execution. "
            "Names, modules and witness hashes are hypotheses/integrity data, "
            "not proof of semantic equivalence."
        ),
    }
