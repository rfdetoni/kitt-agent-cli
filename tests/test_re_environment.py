"""Regression checks for operator approval and lazy RE resource lifecycle."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from kitt import re_environment as re
from kitt import vm_container
from kitt.vm_supervisor import VMError


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.setattr(re, "_CONFIG", tmp_path / "re-environments.json")
    from kitt import re_docker_lab
    monkeypatch.setattr(re_docker_lab, "inspect", lambda system: None)
    profile = tmp_path / "profile.json"
    profile.write_text('{"approved":true}', encoding="utf-8")
    monkeypatch.setattr(
        re, "_profile",
        lambda path, system: (
            Path(path), {"os": system, "kind": "qcow2", "probe_port": 18008, "state": str(tmp_path)},
            "f" * 64,
        ),
    )
    return profile


def test_no_environment_starts_on_status(configured, monkeypatch):
    launches = []
    monkeypatch.setattr(vm_container, "start_vm", lambda *a, **k: launches.append(a))
    observed = re.status()
    assert observed["guests"]["windows"]["status"] == "missing"
    assert observed["guests"]["linux"]["status"] == "missing"
    assert launches == []


def test_lab_run_skips_disabled_or_unenrolled_guests(configured, monkeypatch):
    re.enroll("windows", configured)
    monkeypatch.setattr(re, "ensure", lambda *args, **kwargs: pytest.fail("Unexpected VM boot"))
    assert re.ensure_lab_guests() == []


def test_auto_requires_explicit_opt_in(configured, monkeypatch):
    re.enroll("windows", configured)
    monkeypatch.setattr(vm_container, "operate", lambda *args: {"status": "missing"})
    with pytest.raises(VMError, match="auto-start disabled"):
        re.ensure("windows", automatic=True)


def test_ready_vm_is_reused_not_restarted(configured, monkeypatch):
    re.enroll("windows", configured, auto=True)
    monkeypatch.setattr(vm_container, "operate", lambda *args: {"status": "running"})
    monkeypatch.setattr(vm_container, "start_vm", lambda *args, **kwargs: pytest.fail("Duplicate VM boot"))
    assert re.ensure_lab_guests() == [{"system": "windows", "status": "running", "started": False}]


def test_profile_integrity_must_match_enrollment(configured, monkeypatch):
    re.enroll("windows", configured, auto=True)
    monkeypatch.setattr(re, "_profile", lambda *args: (configured, {}, "0" * 64))
    with pytest.raises(VMError, match="profile changed"):
        re.ensure("windows", automatic=True)


def test_bad_persisted_profile_does_not_auto_start(configured, monkeypatch):
    re.enroll("windows", configured, auto=True)
    config = json.loads(re._CONFIG.read_text())
    config["profiles"]["windows"]["sha256"] = "0" * 64
    re._CONFIG.write_text(json.dumps(config))
    monkeypatch.setattr(vm_container, "start_vm", lambda *args, **kwargs: pytest.fail("Should fail closed"))
    assert re.status()["guests"]["windows"]["status"] == "blocked"



def test_mission_stops_only_guest_started_by_current_run(configured, monkeypatch):
    re.enroll("windows", configured, auto=True)
    operations = []
    def operate(path, action):
        operations.append(action)
        return {"status": "missing" if action == "status" else "exited"}
    monkeypatch.setattr(vm_container, "operate", operate)
    monkeypatch.setattr(vm_container, "start_vm", lambda path: {"status": "ready"})
    with re.managed_lab_guests() as results:
        assert results[0]["started"] is True
        assert operations == ["status"]
    assert operations == ["status", "stop"]


def test_mission_does_not_stop_preexisting_guest(configured, monkeypatch):
    re.enroll("windows", configured, auto=True)
    operations = []
    def operate(path, action):
        operations.append(action)
        return {"status": "running"}
    monkeypatch.setattr(vm_container, "operate", operate)
    monkeypatch.setattr(vm_container, "start_vm", lambda path: pytest.fail("Duplicate boot"))
    with re.managed_lab_guests() as results:
        assert results[0]["started"] is False
    assert operations == ["status"]


def test_guest_is_stopped_after_mission_failure(configured, monkeypatch):
    re.enroll("windows", configured, auto=True)
    operations = []
    monkeypatch.setattr(vm_container, "operate", lambda path, action:
                        operations.append(action) or {"status": "missing"})
    monkeypatch.setattr(vm_container, "start_vm", lambda path: {"status": "ready"})
    with pytest.raises(RuntimeError, match="probe failed"):
        with re.managed_lab_guests():
            raise RuntimeError("probe failed")
    assert operations == ["status", "stop"]


def test_same_process_cannot_lease_guest_twice(configured, monkeypatch):
    re.enroll("windows", configured, auto=True)
    monkeypatch.setattr(vm_container, "operate", lambda path, action: {"status": "running"})
    with re.managed_lab_guests():
        with pytest.raises(VMError, match="already active"):
            with re.managed_lab_guests():
                pass


def test_re_setup_uses_selected_container_engine_in_next_command(monkeypatch):
    monkeypatch.setattr(vm_container, "resolve_runtime", lambda runtime: "docker")
    guide = re.setup("linux")
    assert guide["ready"] is False
    assert "--runtime docker" in guide["next"]
