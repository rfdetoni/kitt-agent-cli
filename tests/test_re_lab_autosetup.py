"""Native KITT lab lifecycle contracts; never require a Windows VM in unit tests."""
from __future__ import annotations

import json
import stat

import pytest

from kitt import re_lab
from kitt.vm_supervisor import VMError


def test_cli_contract_and_dry_run_flags():
    from kitt.cli.main import build_parser
    parsed = build_parser().parse_args([
        "re", "lab", "setup", "--runtime", "docker", "--windows", "11",
        "--linux-profile", "full", "--yes", "--accept-license", "--dry-run",
    ])
    assert parsed.re_action == "lab" and parsed.lab_action == "setup"
    assert parsed.yes and parsed.accept_license and parsed.dry_run
    assert build_parser().parse_args(["re", "lab", "verify", "--full"]).full
    assert build_parser().parse_args(["re", "lab", "down"]).lab_action == "down"
    assert build_parser().parse_args(["re", "lab", "status", "--json"]).lab_json
    assert build_parser().parse_args(["re", "lab", "down", "--dry-run"]).dry_run


def test_setup_requires_explicit_approval_before_touching_host(monkeypatch):
    monkeypatch.setattr(re_lab, "doctor", lambda **kwargs: (_ for _ in ()).throw(
        AssertionError("No preflight before approval")))
    for yes, accept in [(False, False), (True, False), (False, True)]:
        with pytest.raises(VMError, match="--yes and --accept-license"):
            re_lab.setup(yes=yes, accept_license=accept)


def test_preflight_failure_causes_no_directory_mutation(monkeypatch, tmp_path):
    root = tmp_path / "re-lab"
    monkeypatch.setattr(re_lab, "_root", lambda path: root)
    monkeypatch.setattr(re_lab, "doctor", lambda **kw: {
        "ready_for_setup": False, "checks": {"kvm": False, "docker_engine": True},
    })
    with pytest.raises(VMError, match="kvm"):
        re_lab.setup(state_root=root, yes=True, accept_license=True)
    assert not root.exists()


def test_credentials_are_random_private_and_idempotent(tmp_path):
    root = tmp_path / "re-lab"
    (root / "windows").mkdir(parents=True)
    re_lab._credentials(root)
    path = root / "windows.env"
    first = path.read_text()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "PASSWORD=admin" not in first and "AUTOLOGIN=N" in first
    assert len(next(line for line in first.splitlines() if line.startswith("PASSWORD="))) > 40
    re_lab._credentials(root)
    assert path.read_text() == first
    path.write_text("VERSION=11\nUSERNAME=Docker\nPASSWORD=admin\nAUTOLOGIN=Y\n")
    with pytest.raises(VMError, match="Legacy or modified Windows credentials"):
        re_lab._credentials(root)


def test_image_pin_rejects_non_official_digest(monkeypatch, tmp_path):
    oracle = tmp_path / "oracle"
    oracle.mkdir()
    image_id = "sha256:" + "a" * 64
    def fake_call(argv, **kwargs):
        if argv[0] == "pull":
            return ""
        if "{{.Id}}" in argv:
            return image_id
        return json.dumps(["evil.example/other/windows@sha256:" + "b" * 64])
    monkeypatch.setattr(re_lab, "_call", fake_call)
    with pytest.raises(VMError, match="official"):
        re_lab._image_lock(tmp_path)
    assert not (oracle / "image.lock.json").exists()


def test_image_digest_is_locked_and_changes_fail_closed(monkeypatch, tmp_path):
    (tmp_path / "oracle").mkdir()
    values = {"digest": "b" * 64}
    def fake_call(argv, **kwargs):
        if argv[0] == "pull":
            return ""
        if "{{.Id}}" in argv:
            return "sha256:" + "a" * 64
        return json.dumps(["dockurr/windows@sha256:" + values["digest"]])
    monkeypatch.setattr(re_lab, "_call", fake_call)
    assert re_lab._image_lock(tmp_path) == "sha256:" + "a" * 64
    assert re_lab._image_lock(tmp_path) == "sha256:" + "a" * 64
    values["digest"] = "c" * 64
    with pytest.raises(VMError, match="digest changed"):
        re_lab._image_lock(tmp_path)


def test_existing_guest_is_preserved_without_bootstrap(monkeypatch, tmp_path):
    root = tmp_path / "re-lab"
    monkeypatch.setattr(re_lab, "_root", lambda p: root)
    monkeypatch.setattr(re_lab, "doctor", lambda **kwargs: {"ready_for_setup": True})
    monkeypatch.setattr(re_lab.re_docker_lab, "inspect", lambda system:
        {"container_id": "a" * 64, "workspace": str(root / "workspace")}
        if system == "linux" else {"container_id": "b" * 64})
    monkeypatch.setattr(re_lab, "_linux", lambda root: (_ for _ in ()).throw(
        AssertionError("No replacement of existing lab")))
    monkeypatch.setattr(re_lab, "status", lambda **kw: {"guests": {"windows": "running"}})
    result = re_lab.setup(state_root=root, yes=True, accept_license=True)
    assert result["status"] == "EXISTING_UNVERIFIED"


def test_fresh_setup_reuses_bootstrap_not_a_second_windows_creator(monkeypatch, tmp_path):
    root = tmp_path / "re-lab"
    monkeypatch.setattr(re_lab, "_root", lambda p: root)
    monkeypatch.setattr(re_lab, "doctor", lambda **kw: {"ready_for_setup": True})
    monkeypatch.setattr(re_lab.re_docker_lab, "inspect", lambda system: None)
    calls = []
    monkeypatch.setattr(re_lab, "_linux", lambda path: calls.append("linux"))
    monkeypatch.setattr(re_lab, "_image_lock", lambda path: "sha256:" + "a" * 64)
    monkeypatch.setattr(re_lab.re_oracle_bootstrap, "bootstrap",
        lambda **kwargs: (calls.append("oem") or {"oracle_ready": False}))
    report = re_lab.setup(state_root=root, yes=True, accept_license=True)
    assert calls == ["linux", "oem"]
    assert report["status"] == "PROVISIONING"
    assert (root / "windows.env").is_file()
    assert report["oracle_ready"] is False


def test_operator_up_down_use_inspected_containers(monkeypatch, tmp_path):
    monkeypatch.setattr(re_lab, "_root", lambda p: tmp_path / "re-lab")
    monkeypatch.setattr(re_lab.re_docker_lab, "inspect", lambda role: (
        {"status": "running", "container_id": "a" * 64,
         **({"workspace": str(tmp_path / "re-lab" / "workspace")} if role == "linux" else {})}))
    calls = []
    monkeypatch.setattr(re_lab.re_docker_lab, "operate",
                        lambda role, action: (calls.append((role, action)) or {"changed": False}))
    re_lab.operate("down")
    re_lab.operate("up")
    assert calls == [("windows", "stop"), ("linux", "stop"),
                     ("linux", "start"), ("windows", "start")]


def test_verify_missing_manifest_never_contacts_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(re_lab, "_root", lambda p: tmp_path / "re-lab")
    monkeypatch.setattr(re_lab.re_oracle, "doctor",
                        lambda **kwargs: (_ for _ in ()).throw(AssertionError("SSH unexpectedly used")))
    assert re_lab.verify()["status"] == "BLOCKED_ENV"
