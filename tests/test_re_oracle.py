"""Windows Oracle readiness: SSH identity, immutable allowlist, no probe execution."""
from __future__ import annotations

import hashlib
import json
import platform
from pathlib import Path
from unittest.mock import patch

import pytest

from kitt import re_oracle
from kitt.extensions.builtin_plugins.reverse_engineering import guest_rpc, mission
from kitt.extensions.builtin_plugins.reverse_engineering.probe_runner import ProbeError
from kitt.vm_supervisor import VMError


@pytest.fixture
def probe_manifest(tmp_path):
    root = tmp_path / "probes"
    root.mkdir()
    file = root / "event.exe"
    file.write_bytes(b"benign-fixture")
    entry = {"path": "event.exe", "sha256": hashlib.sha256(file.read_bytes()).hexdigest()}
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps({"probes": {"event_auto_reset": entry}}), encoding="utf-8")
    return root, manifest, hashlib.sha256(manifest.read_bytes()).hexdigest()


def test_guest_info_is_read_only_and_restricts_manifest(probe_manifest):
    root, manifest, digest = probe_manifest
    with patch.object(guest_rpc, "run_probe", side_effect=AssertionError("must not execute")):
        info = guest_rpc.handle({"version": 1, "method": "probe.info", "probe": None},
                                root, manifest)
    assert info["version"] == 1
    assert info["guest"]["platform"] == platform.system()
    assert info["guest"]["manifest_sha256"] == digest
    assert info["guest"]["probe_count"] == 1
    for bad in (
        {"version": 1, "method": "probe.info", "probe": "event_auto_reset"},
        {"version": 1, "method": "probe.info", "probe": None, "cmd": "shell"},
        {"version": 1, "method": "shell.run", "probe": None},
    ):
        with pytest.raises((ProbeError, ValueError)):
            guest_rpc.handle(bad, root, manifest)


def test_guest_info_rejects_duplicate_and_outside_manifest(probe_manifest):
    root, manifest, _ = probe_manifest
    manifest.write_text('{"probes":{"one":{},"one":{}}}', encoding="utf-8")
    with pytest.raises(ProbeError):
        guest_rpc.handle({"version": 1, "method": "probe.info", "probe": None}, root, manifest)
    manifest.unlink()
    manifest.symlink_to(root / "other.json")
    with pytest.raises(ProbeError):
        guest_rpc.handle({"version": 1, "method": "probe.info", "probe": None}, root, manifest)


def test_guest_info_blocks_missing_or_changed_pinned_binary(probe_manifest):
    root, manifest, _ = probe_manifest
    exe = root / "event.exe"
    exe.write_bytes(b"modified")
    with pytest.raises(ProbeError, match="changed"):
        guest_rpc.handle({"version": 1, "method": "probe.info", "probe": None},
                         root, manifest)
    exe.unlink()
    with pytest.raises(ProbeError, match="Invalid probe file"):
        guest_rpc.handle({"version": 1, "method": "probe.info", "probe": None},
                         root, manifest)


def test_probe_guest_info_checks_platform_and_schema(monkeypatch):
    request = []
    def exchange(hosts, alias, body, *, timeout):
        request.append((alias, body, timeout))
        return {"version": 1, "guest": {"platform": "Windows", "release": "11",
                "architecture": "AMD64", "manifest_sha256": "a" * 64, "probe_count": 1}}
    monkeypatch.setattr(mission, "_ssh_exchange", exchange)
    identity = mission.probe_guest_info({"reference": "windows-test", "candidate": "wine"})
    assert identity["platform"] == "Windows"
    assert request == [("windows-test", {"version": 1, "method": "probe.info", "probe": None}, 20)]
    monkeypatch.setattr(mission, "_ssh_exchange", lambda *args, **kwargs:
                        {"version": 1, "guest": {**identity, "platform": "Linux+Wine"}})
    with pytest.raises(mission.MissionError, match="identity"):
        mission.probe_guest_info({"reference": "windows-test", "candidate": "wine"})


def test_oracle_doctor_fails_closed_without_expected_manifest(monkeypatch):
    monkeypatch.setattr(re_oracle.re_docker_lab, "inspect",
                        lambda system: {"status": "running", "container": "kitt-re-win11"})
    assert re_oracle.doctor(manifest_sha256="")["oracle_ready"] is False
    assert re_oracle.doctor(manifest_sha256="a" * 63)["oracle_ready"] is False
    monkeypatch.setattr(re_oracle, "_hosts", lambda _: {"reference": "windows-test", "candidate": "wine"})
    good = {"platform": "Windows", "release": "11", "architecture": "AMD64",
            "manifest_sha256": "b" * 64, "probe_count": 1}
    monkeypatch.setattr(re_oracle, "probe_guest_info", lambda hosts: good)
    assert re_oracle.doctor(manifest_sha256="a" * 64)["status"] == "BLOCKED_ENV"
    ready = re_oracle.doctor(manifest_sha256="b" * 64)
    assert ready["oracle_ready"] is True
    assert ready["guest_attested"] is False
    assert ready["transport"] == "operator-pinned-ssh-forced-command"
    monkeypatch.setattr(re_oracle.re_docker_lab, "inspect",
                        lambda system: {"status": "exited", "container": "kitt-re-win11"})
    assert re_oracle.doctor(manifest_sha256="b" * 64)["status"] == "BLOCKED_ENV"


def test_oracle_doctor_rejects_untrusted_docker_and_ssh(monkeypatch):
    monkeypatch.setattr(re_oracle.re_docker_lab, "inspect",
                        lambda system: (_ for _ in ()).throw(VMError("bad labels")))
    assert re_oracle.doctor(manifest_sha256="a" * 64)["oracle_ready"] is False
    monkeypatch.setattr(re_oracle.re_docker_lab, "inspect",
                        lambda system: {"status": "running", "container": "kitt-re-win11"})
    monkeypatch.setattr(re_oracle, "_hosts",
                        lambda hosts: (_ for _ in ()).throw(mission.MissionError("untrusted profile")))
    assert re_oracle.doctor(manifest_sha256="a" * 64)["status"] == "BLOCKED_ENV"


def test_oracle_cli_args_are_explicit_and_opt_in():
    from kitt.cli.main import build_parser
    parser = build_parser()
    oracle = parser.parse_args(["re", "oracle", "doctor", "--manifest-sha256", "a" * 64])
    assert oracle.re_action == "oracle"
    assert oracle.oracle_action == "doctor"
    assert oracle.manifest_sha256 == "a" * 64
    default = parser.parse_args(["re", "status"])
    assert default.check_oracle is False
    checked = parser.parse_args(["re", "status", "--check-oracle",
                                "--manifest-sha256", "b" * 64])
    assert checked.check_oracle is True
    assert checked.manifest_sha256 == "b" * 64
