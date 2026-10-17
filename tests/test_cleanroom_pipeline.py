"""Clean-room boundary tests (no Docker, VM or model is required)."""
import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from kitt import cleanroom_handoff
from kitt.cleanroom_pipeline import (
    CleanRoomEnvironmentError, _git_changes, _trusted_script, run_pipeline,
)


def _git_repo(tmp_path):
    root = tmp_path / "wine"
    (root / "dlls" / "kernel32").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    file = root / "dlls" / "kernel32" / "event.c"
    file.write_text("int before = 1;\n")
    subprocess.run(["git", "-C", str(root), "-c", "user.name=KITT",
                    "-c", "user.email=test@example.invalid", "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=KITT",
                    "-c", "user.email=test@example.invalid", "commit", "-qm", "baseline"], check=True)
    return root, file


def _seal(tmp_path):
    file = tmp_path / "spec.json"
    file.write_text(json.dumps({
        "target_dll": "kernel32.dll", "function_name": "CreateEventW",
        "calling_convention": "WINAPI",
        "signature": {"return_type": "HANDLE", "parameters": []},
        "observed_behaviors": [{
            "case_id": "case1", "input_conditions": "named event absent",
            "expected_return": "handle", "last_error": "ERROR_SUCCESS",
            "side_effects": "New event handle",
        }],
    }))
    private = tmp_path / "private"
    return private, cleanroom_handoff.seal(file, reviewer="independent", state_root=private)


def test_rejects_changes_outside_authorized_dll(tmp_path):
    wine, file = _git_repo(tmp_path)
    file.write_text("int before = 2;\n")
    result = _git_changes(wine, "dlls/kernel32/")
    assert result["changed_count"] == 1
    rogue = wine / "dlls" / "user32" / "wrong.c"
    rogue.parent.mkdir()
    rogue.write_text("int leak=1;\n")
    with pytest.raises(CleanRoomEnvironmentError, match="outside approved"):
        _git_changes(wine, "dlls/kernel32/")


def test_refuses_unsigned_test_plan(tmp_path):
    script = tmp_path / "run.sh"
    script.write_text("#!/bin/sh\nset -eu\nexit 0\n")
    signature = tmp_path / "run.sig"
    signature.write_bytes(b"x" * 64)
    with pytest.raises(CleanRoomEnvironmentError):
        _trusted_script(script, signature,
                        hashlib.sha256(script.read_bytes()).hexdigest())


def test_pipeline_fails_closed_without_signed_plan(tmp_path):
    wine, _ = _git_repo(tmp_path)
    private, sealed = _seal(tmp_path)
    script = tmp_path / "run.sh"
    script.write_text("#!/bin/sh\nset -eu\nexit 0\n")
    with pytest.raises(CleanRoomEnvironmentError):
        run_pipeline(
            sealed["run_id"], expected_sha256=sealed["contract_sha256"],
            wine_source=wine, implementer_image="sha256:" + "1" * 64,
            validator_image="sha256:" + "2" * 64,
            test_script=script, test_signature=tmp_path / "missing.sig",
            test_sha256=hashlib.sha256(script.read_bytes()).hexdigest(),
            state_root=private, approved=True,
        )


def test_pipeline_never_claims_conformance_from_green_tests(tmp_path, monkeypatch):
    wine, file = _git_repo(tmp_path)
    private, sealed = _seal(tmp_path)
    script = tmp_path / "run.sh"
    script.write_text("#!/bin/sh\nset -eu\nexit 0\n")
    monkeypatch.setattr("kitt.cleanroom_pipeline._trusted_script",
                        lambda *_: (script, script.read_bytes()))
    def implement(*_a, **_k):
        file.write_text("int before = 2;\n")
        return {"status": "IMPLEMENTER_EXITED_UNVERIFIED", "exit_code": 0}
    monkeypatch.setattr("kitt.cleanroom_pipeline.run_offline_implementer", implement)
    monkeypatch.setattr("kitt.cleanroom_pipeline._validate_independently",
                        lambda *_a, **_k: {"status": "TEST_COMMAND_EXIT_0_NOT_ATTESTED",
                                           "exit_code": 0})
    result = run_pipeline(
        sealed["run_id"], expected_sha256=sealed["contract_sha256"],
        wine_source=wine, implementer_image="sha256:" + "1" * 64,
        validator_image="sha256:" + "2" * 64,
        test_script=script, test_signature=tmp_path / "missing.sig",
        test_sha256=hashlib.sha256(script.read_bytes()).hexdigest(),
        state_root=private, approved=True,
    )
    assert result["status"] == "NEEDS_INDEPENDENT_ORACLE_CONFORMANCE"
    assert result["new_code_oracle_match_verified"] is False
    logs = list((private / sealed["run_id"]).glob("audit-*.json"))
    assert len(logs) == 3


def test_pipeline_rejects_validator_mutating_implementation(tmp_path, monkeypatch):
    wine, file = _git_repo(tmp_path)
    private, sealed = _seal(tmp_path)
    script = tmp_path / "run.sh"
    script.write_text("#!/bin/sh\nset -eu\nexit 0\n")
    monkeypatch.setattr("kitt.cleanroom_pipeline._trusted_script",
                        lambda *_: (script, script.read_bytes()))
    def implement(*_a, **_k):
        file.write_text("int before = 2;\n")
        return {"status": "IMPLEMENTER_EXITED_UNVERIFIED", "exit_code": 0}
    def test_runner(*_a, **_k):
        file.write_text("int before = 3;\n")
        return {"status": "TEST_COMMAND_EXIT_0_NOT_ATTESTED", "exit_code": 0}
    monkeypatch.setattr("kitt.cleanroom_pipeline.run_offline_implementer", implement)
    monkeypatch.setattr("kitt.cleanroom_pipeline._validate_independently", test_runner)
    with pytest.raises(CleanRoomEnvironmentError, match="test.*changed"):
        run_pipeline(
            sealed["run_id"], expected_sha256=sealed["contract_sha256"],
            wine_source=wine, implementer_image="sha256:" + "1" * 64,
            validator_image="sha256:" + "2" * 64,
            test_script=script, test_signature=tmp_path / "missing.sig",
            test_sha256=hashlib.sha256(script.read_bytes()).hexdigest(),
            state_root=private, approved=True,
        )
