"""Smoke and adversarial tests for operator-reviewed clean-room seals."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from kitt import cleanroom_handoff
from kitt_protocol.cleanroom import CleanRoomContractError


def _contract():
    return {
        "target_dll": "kernel32.dll", "function_name": "CloseHandle",
        "calling_convention": "WINAPI",
        "signature": {"return_type": "BOOL", "parameters": [
            {"name": "hObject", "c_type": "HANDLE", "direction": "IN"}]},
        "observed_behaviors": [{"case_id": "invalid",
            "input_conditions": "hObject=NULL", "expected_return": "FALSE",
            "last_error": "ERROR_INVALID_HANDLE",
            "side_effects": "No public state changes"}],
    }


def _write(root: Path, value=None) -> Path:
    path = root / "sample.json"
    path.write_text(json.dumps(value or _contract()), encoding="utf-8")
    return path


def test_re_seal_validates_and_never_executes(tmp_path):
    source = _write(tmp_path)
    dest = tmp_path / "private"
    parsed = cleanroom_handoff.validate(source)
    assert parsed["status"] == "VALID_NEEDS_INDEPENDENT_REVIEW"
    result = cleanroom_handoff.seal(source, reviewer="independent.reviewer", state_root=dest)
    assert result["status"] == "SEALED_NOT_EXECUTABLE"
    assert result["isolation_ready"] is False
    assert (dest / result["run_id"] / "contract.json").is_file()
    verified = cleanroom_handoff.verify(result["run_id"],
        expected_sha256=result["contract_sha256"], state_root=dest)
    assert verified["status"] == "VERIFIED_NOT_EXECUTABLE"


def test_re_seal_rejects_unreviewed_and_symlink(tmp_path):
    source = _write(tmp_path)
    with pytest.raises(ValueError):
        cleanroom_handoff.seal(source, reviewer="", state_root=tmp_path / "private")
    link = tmp_path / "alias.json"
    link.symlink_to(source)
    with pytest.raises((OSError, CleanRoomContractError)):
        cleanroom_handoff.validate(link)


def test_re_seal_rejects_tamper_and_duplicate_case(tmp_path):
    root = tmp_path / "private"
    sealed = cleanroom_handoff.seal(_write(tmp_path),
                                   reviewer="reviewer.one", state_root=root)
    with pytest.raises(PermissionError):
        cleanroom_handoff.verify(sealed["run_id"], expected_sha256="0" * 64, state_root=root)
    bad = _contract()
    bad["observed_behaviors"].append(bad["observed_behaviors"][0])
    source = _write(tmp_path, bad)
    with pytest.raises(CleanRoomContractError):
        cleanroom_handoff.seal(source, reviewer="reviewer.one", state_root=root)


def test_re_seal_blocks_public_storage(tmp_path):
    source = _write(tmp_path)
    root = tmp_path / "private"
    root.mkdir()
    root.chmod(0o755)
    with pytest.raises(PermissionError):
        cleanroom_handoff.seal(source, reviewer="reviewer.one", state_root=root)
