"""Operator-controlled clean-room handoff preparation.

No model text or file permissions confer the authority to launch an implementer.
This module only seals a reviewed, strictly validated black-box specification.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import time
import uuid

from kitt_protocol.cleanroom import (
    CleanRoomContractError, canonical_cleanroom_bytes, decode_cleanroom_contract,
)

_MAX_SOURCE = 65536


def _secure_root(directory: Path) -> Path:
    """Prevent public or symlink-backed state; this is a Linux RE-only feature."""
    if os.name != "posix":
        raise PermissionError("Clean-room sealing requires POSIX operator-owned state")
    directory = Path(directory).expanduser()
    if directory.exists() and directory.is_symlink():
        raise PermissionError("Clean-room root cannot be a symlink")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = directory.resolve(strict=True)
    mode = directory.stat()
    if (not stat.S_ISDIR(mode.st_mode) or mode.st_uid != os.getuid()
            or mode.st_mode & 0o077):
        raise PermissionError("Clean-room root must be private, mode 0700, operator-owned")
    return directory


def _read_source(path: Path) -> bytes:
    path = Path(path).expanduser()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb", closefd=True) as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_SOURCE:
            raise CleanRoomContractError("source must be a bounded regular file")
        raw = handle.read(_MAX_SOURCE + 1)
    if len(raw) > _MAX_SOURCE:
        raise CleanRoomContractError("contract file exceeds byte limit")
    return raw


def validate(path: Path) -> dict:
    """Return only non-sensitive contract metadata; never echo source text."""
    value = decode_cleanroom_contract(_read_source(path))
    canonical = canonical_cleanroom_bytes(value)
    return {
        "status": "VALID_NEEDS_INDEPENDENT_REVIEW",
        "contract_sha256": hashlib.sha256(canonical).hexdigest(),
        "function_name": value["function_name"],
        "target_dll": value["target_dll"],
        "cases": len(value["observed_behaviors"]),
    }


def _create_private_file(parent: Path, name: str, data: bytes) -> None:
    fd = os.open(parent / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=True) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        (parent / name).unlink(missing_ok=True)
        raise


def seal(path: Path, *, reviewer: str, state_root: Path | None = None) -> dict:
    """Seal a reviewed black-box contract into fresh, private operator-owned storage.

    Does NOT authorize or execute a child/worker. Human attestation is not
    independently verifiable merely because a reviewer identifier was supplied.
    """
    if not isinstance(reviewer, str) or not 2 <= len(reviewer) <= 80 or not all(
        char.isascii() and (char.isalnum() or char in "._-@") for char in reviewer
    ):
        raise ValueError("explicit reviewer identifier required")
    root = _secure_root(state_root or (Path.home() / ".local/share/kitt/cleanroom"))
    value = decode_cleanroom_contract(_read_source(path))
    canonical = canonical_cleanroom_bytes(value)
    digest = hashlib.sha256(canonical).hexdigest()
    run_id = uuid.uuid4().hex
    output = root / run_id
    output.mkdir(mode=0o700)
    try:
        _create_private_file(output, "contract.json", canonical + b"\n")
        receipt = {
            "schema": "kitt.cleanroom.handoff.v1",
            "run_id": run_id,
            "contract_sha256": digest,
            "reviewer_claim": reviewer,
            "created_unix_seconds_untrusted": int(time.time()),
            "status": "SEALED_NOT_EXECUTABLE",
        }
        _create_private_file(output, "receipt.json", (
            json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("ascii"))
        fd = os.open(output, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except BaseException:
        for name in ("contract.json", "receipt.json"):
            (output / name).unlink(missing_ok=True)
        output.rmdir()
        raise
    return {
        "status": "SEALED_NOT_EXECUTABLE",
        "run_id": run_id,
        "contract_sha256": digest,
        "source": "black_box_only",
        "isolation_ready": False,
        "next_gate": "independent workspace/container isolation and clean-session handoff",
    }


def verify(run_id: str, *, expected_sha256: str, state_root: Path | None = None) -> dict:
    if not isinstance(run_id, str) or len(run_id) != 32 or any(
        char not in "0123456789abcdef" for char in run_id
    ):
        raise ValueError("invalid run_id")
    if (not isinstance(expected_sha256, str) or len(expected_sha256) != 64 or any(
        char not in "0123456789abcdef" for char in expected_sha256
    )):
        raise ValueError("trusted expected SHA-256 is required")
    root = _secure_root(state_root or (Path.home() / ".local/share/kitt/cleanroom"))
    directory = root / run_id
    if directory.is_symlink() or not directory.is_dir():
        raise PermissionError("untrusted seal directory")
    raw = _read_source(directory / "contract.json")
    value = decode_cleanroom_contract(raw)
    digest = hashlib.sha256(canonical_cleanroom_bytes(value)).hexdigest()
    if digest != expected_sha256:
        raise PermissionError("clean-room contract digest mismatch")
    return {"status": "VERIFIED_NOT_EXECUTABLE", "run_id": run_id,
            "contract_sha256": digest, "isolation_ready": False}
