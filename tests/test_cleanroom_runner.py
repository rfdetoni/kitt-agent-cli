"""OCI clean-room runner tool-surface boundary regression tests."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from kitt import cleanroom_handoff
from kitt.cleanroom_runner import CleanRoomEnvironmentError, run_offline_implementer

IMAGE = "sha256:" + "a" * 64


def _fixture(tmp_path):
    wine = tmp_path / "wine"
    wine.mkdir()
    (wine / ".git").mkdir()
    (wine / ".git" / "config").write_text("[core]\n\trepositoryformatversion = 0\n")
    source = tmp_path / "spec.json"
    source.write_text(json.dumps({
        "target_dll": "kernel32.dll",
        "function_name": "CloseHandle",
        "calling_convention": "WINAPI",
        "signature": {"return_type": "BOOL", "parameters": []},
        "observed_behaviors": [{
            "case_id": "null", "input_conditions": "hObject=NULL",
            "expected_return": "FALSE", "last_error": "ERROR_INVALID_HANDLE",
            "side_effects": "None visible",
        }],
    }))
    root = tmp_path / "private"
    sealed = cleanroom_handoff.seal(source, reviewer="reviewer", state_root=root)
    return wine, root, sealed


def test_denies_missing_operator_approval_and_mutable_images(tmp_path):
    wine, root, sealed = _fixture(tmp_path)
    kwargs = dict(run_id=sealed["run_id"], expected_sha256=sealed["contract_sha256"],
                  wine_source=wine, state_root=root, image=IMAGE)
    with pytest.raises(PermissionError):
        run_offline_implementer(**kwargs)
    with pytest.raises(CleanRoomEnvironmentError):
        run_offline_implementer(**{**kwargs, "image": "wine:latest"}, approved=True)


def test_only_approved_spec_and_implementer_checkout_mounted(tmp_path, monkeypatch):
    wine, root, sealed = _fixture(tmp_path)
    commands = []
    def fake_run(argv, **kw):
        commands.append(argv)
        if "image" in argv and "inspect" in argv:
            return SimpleNamespace(returncode=0, stdout=(IMAGE + "\n").encode())
        kw["stdout"].write(b"offline pass")
        kw["stderr"].write(b"")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr("kitt.cleanroom_runner.shutil.which", lambda x: "/usr/bin/docker")
    monkeypatch.setattr("kitt.cleanroom_runner._verify_independent_review", lambda *_: None)
    monkeypatch.setattr("kitt.cleanroom_runner.subprocess.run", fake_run)
    result = run_offline_implementer(
        sealed["run_id"], expected_sha256=sealed["contract_sha256"],
        wine_source=wine, state_root=root, image=IMAGE, approved=True,
    )
    assert result["status"] == "IMPLEMENTER_EXITED_UNVERIFIED"
    cmd = commands[-1]
    assert cmd.count("--mount") == 2
    assert cmd[cmd.index("--network") + 1] == "none"
    assert cmd[cmd.index("--cap-drop") + 1] == "ALL"
    assert "--read-only" in cmd and "no-new-privileges" in cmd
    assert cmd[1:3] == ["--context", "default"]
    assert any("dst=/cleanroom/contract.json,readonly" in item for item in cmd)
    assert "--entrypoint" in cmd
    assert result["independent_winetest_passed"] is False
    assert result["oracle_match_verified"] is False


def test_refuse_wine_checkout_containing_private_seals(tmp_path, monkeypatch):
    wine, root, sealed = _fixture(tmp_path)
    monkeypatch.setattr("kitt.cleanroom_runner.shutil.which", lambda x: "/usr/bin/docker")
    with pytest.raises(CleanRoomEnvironmentError):
        run_offline_implementer(
            sealed["run_id"], expected_sha256=sealed["contract_sha256"],
            wine_source=root, state_root=root, image=IMAGE, approved=True,
        )


def test_denies_unsigned_handoff_before_container_execution(tmp_path, monkeypatch):
    wine, root, sealed = _fixture(tmp_path)
    monkeypatch.setattr("kitt.cleanroom_runner.shutil.which", lambda x: "/usr/bin/docker")
    with pytest.raises(CleanRoomEnvironmentError, match="reviewer"):
        run_offline_implementer(
            sealed["run_id"], expected_sha256=sealed["contract_sha256"],
            wine_source=wine, state_root=root, image=IMAGE, approved=True,
        )
