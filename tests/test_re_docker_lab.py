"""Regression cases for installer-managed Docker RE lab identity and lifecycle."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from kitt import re_docker_lab as lab, re_environment as re, vm_container
from kitt.vm_supervisor import VMError


@pytest.fixture
def docker_lab(tmp_path, monkeypatch):
    root = tmp_path / "re-lab"
    root.mkdir(mode=0o700)
    (root / "workspace").mkdir(mode=0o700)
    (root / "windows").mkdir(mode=0o700)
    cid = "a" * 64
    states = {"linux": "running", "windows": "running"}
    calls = []

    def record(system):
        name, role, source, dest = lab._ROLES[system]
        host = ({"PortBindings": {"8006/tcp": [
                    {"HostIp": "127.0.0.1", "HostPort": "8006"}]}}
                if system == "windows" else
                {"NetworkMode": "none", "Privileged": False, "CapDrop": ["ALL"]})
        return {
            "Id": cid, "Image": "sha256:" + "b" * 64, "Name": "/" + name,
            "Config": {"Labels": {"dev.kitt.re-lab": "v1",
                                  "dev.kitt.re-role": role,
                                  "dev.kitt.re-root": str(root)}},
            "Mounts": [{"Type": "bind", "Source": str(root / source),
                        "Destination": dest}],
            "HostConfig": host, "State": {"Status": states[system]},
        }

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[1:3] == ["container", "inspect"]:
            system = "windows" if argv[-1] == "kitt-re-win11" else "linux"
            return SimpleNamespace(returncode=0, stdout=json.dumps([record(system)]).encode(), stderr=b"")
        if argv[1] in {"start", "stop"}:
            assert argv[2] == cid
            states["linux"] = "running" if argv[1] == "start" else "exited"
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        raise AssertionError(argv)

    monkeypatch.setattr(lab.shutil, "which", lambda binary: "/usr/bin/docker")
    monkeypatch.setattr(lab.sys, "platform", "linux")
    monkeypatch.setattr(lab.subprocess, "run", run)
    monkeypatch.setattr(vm_container, "resolve_runtime", lambda x: "docker")
    return root, record, calls, states


def test_inventory_detects_containers_without_granting_oracle(docker_lab):
    root, record, calls, states = docker_lab
    linux, windows = lab.inspect("linux"), lab.inspect("windows")
    assert linux["environment"] == "linux-toolbox"
    assert linux["workspace"] == str(root / "workspace")
    assert windows["console"] == "http://127.0.0.1:8006"
    assert linux["oracle_ready"] is False and windows["oracle_ready"] is False
    assert len(calls) == 2 and all("inspect" in call for call in calls)


def test_re_status_and_setup_reuse_managed_lab_without_mutation(tmp_path, docker_lab, monkeypatch):
    monkeypatch.setattr(re, "_CONFIG", tmp_path / "absent.json")
    result = re.status()
    assert result["guests"]["windows"]["status"] == "running"
    assert result["guests"]["linux"]["backend"] == "docker"
    assert result["guests"]["linux"]["auto"] is False
    assert re.setup("linux")["ready"] is True
    assert re.setup("windows")["ready"] is True
    assert re.ensure_lab_guests() == []
    assert all("inspect" in call for call in docker_lab[2])


def test_operator_start_stop_by_immutable_id_never_recreates(tmp_path, docker_lab, monkeypatch):
    monkeypatch.setattr(re, "_CONFIG", tmp_path / "absent.json")
    _, _, calls, states = docker_lab
    assert re.ensure("linux")["changed"] is False
    assert re.stop("linux")["status"] == "exited"
    assert re.ensure("linux")["status"] == "running"
    assert [cmd[1] for cmd in calls if cmd[1] in {"start", "stop"}] == ["stop", "start"]
    with pytest.raises(VMError, match="auto-enrolled"):
        re.ensure("windows", automatic=True)


def test_impostor_labels_and_host_mounts_are_rejected(docker_lab):
    root, record, calls, states = docker_lab
    wrong_role = record("windows")
    wrong_role["Config"]["Labels"]["dev.kitt.re-role"] = "other"
    with pytest.raises(VMError, match="not owned"):
        lab._checked(wrong_role, "windows")
    wrong_mount = record("linux")
    wrong_mount["Mounts"][0]["Source"] = "/tmp/wrong"
    with pytest.raises(VMError, match="mount"):
        lab._checked(wrong_mount, "linux")
    bridge = record("linux")
    bridge["HostConfig"]["NetworkMode"] = "bridge"
    with pytest.raises(VMError, match="isolation"):
        lab._checked(bridge, "linux")
    exposed = record("windows")
    exposed["HostConfig"]["PortBindings"]["8006/tcp"][0]["HostIp"] = "0.0.0.0"
    with pytest.raises(VMError, match="localhost"):
        lab._checked(exposed, "windows")


def test_missing_container_is_not_a_registered_guest(tmp_path, monkeypatch):
    monkeypatch.setattr(lab.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(lab.sys, "platform", "linux")
    monkeypatch.setattr(lab.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=1, stderr=b"No such container", stdout=b""))
    monkeypatch.setattr(re, "_CONFIG", tmp_path / "absent.json")
    assert re.status()["guests"]["windows"]["status"] == "missing"
