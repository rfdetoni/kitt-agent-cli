"""Disposable Dockur bootstrap safety, reproducible OEM and CLI gates."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from kitt import re_oracle_bootstrap as bootstrap
from kitt.vm_supervisor import VMError


def test_windows_oracle_oem_assets_are_packaged():
    for asset in ("install.bat", "kitt-bootstrap.ps1", "kitt-guest-rpc.ps1", "event_probe.c"):
        path = bootstrap.ASSETS / asset
        assert path.is_file()
        assert 64 <= path.stat().st_size <= 65536


def test_bootstrap_never_resets_without_both_operator_gates(monkeypatch):
    def unexpected():
        raise AssertionError("Should not touch Docker/disk without explicit approval")
    monkeypatch.setattr(bootstrap, "_lab", unexpected)
    for recreate, yes, accept in [(False, False, False), (True, False, True),
                                  (True, True, False)]:
        with pytest.raises(VMError, match="--yes and --accept-license"):
            bootstrap.bootstrap(recreate=recreate, yes=yes, accept_license=accept)


def test_bootstrap_existing_guest_requires_explicit_recreate(monkeypatch, tmp_path):
    lab = tmp_path / "re-lab"
    (lab / "windows").mkdir(parents=True)
    (lab / ".lock").touch()
    monkeypatch.setattr(bootstrap, "_lab", lambda: (lab, {"container_id": "a" * 64}))
    monkeypatch.setattr(bootstrap.re_docker_lab, "inspect", lambda role: {"container_id": "b" * 64})
    monkeypatch.setattr(bootstrap, "prepare",
                        lambda: (_ for _ in ()).throw(AssertionError("No bootstrap before approval")))
    with pytest.raises(VMError, match="--recreate"):
        bootstrap.bootstrap(recreate=False, yes=True, accept_license=True)


def test_prepared_oem_keys_and_manifest_are_host_pinned(tmp_path, monkeypatch):
    lab = tmp_path / "re-lab"
    (lab / "workspace").mkdir(parents=True, mode=0o700)
    (lab / "windows").mkdir(mode=0o700)
    linux = {"workspace": str(lab / "workspace"), "status": "running", "container_id": "a" * 64}
    monkeypatch.setattr(bootstrap, "_lab", lambda: (lab, linux))
    def fake_key(path):
        path.write_bytes(b"private")
        path.chmod(0o600)
        path.with_suffix(".pub").write_text("ssh-ed25519 YWFhYWJiYg== test\n")
    monkeypatch.setattr(bootstrap, "_key", fake_key)
    def fake_compiler(lab, linux, stage):
        data = b"MZ" + b"test" * 80
        bootstrap._write(stage / "event_probe.exe", data)
        return data
    monkeypatch.setattr(bootstrap, "_compile_probe", fake_compiler)
    plan = bootstrap.prepare()
    payload = json.loads((lab / "oracle/oem/manifest.json").read_text())
    assert payload["probes"]["event_auto_reset"]["sha256"] == plan["probe_sha256"]
    assert hashlib.sha256((lab / "oracle/oem/manifest.json").read_bytes()).hexdigest() == plan["manifest_sha256"]
    assert "127.0.0.1" in (lab / "oracle/ssh_config").read_text()
    assert "StrictHostKeyChecking yes" in (lab / "oracle/ssh_config").read_text()
    assert "ssh-ed25519 " in (lab / "oracle/known_hosts").read_text()
    assert "kitt-win-oracle" == json.loads((lab / "oracle/hosts.json").read_text())["reference"]
    assert (lab / "oracle/oem/ssh_host_ed25519_key").stat().st_mode & 0o077 == 0


def test_reset_rejects_symlink_before_removing_files(tmp_path):
    lab = tmp_path / "re-lab"
    storage = lab / "windows"
    storage.mkdir(parents=True)
    victim = tmp_path / "unrelated.txt"
    victim.write_text("keep")
    (storage / "unsafe").symlink_to(victim)
    with pytest.raises(VMError, match="symbolic links"):
        bootstrap._remove_disk(storage, lab)
    assert victim.read_text() == "keep"
    assert storage.exists()


def test_reset_sequence_stops_verified_container_only_after_preflight(tmp_path, monkeypatch):
    lab = tmp_path / "re-lab"
    (lab / "windows").mkdir(parents=True)
    (lab / "workspace").mkdir(mode=0o700)
    (lab / ".lock").touch()
    settings = lab / "windows.env"
    settings.write_text("VERSION=11\n")
    settings.chmod(0o600)
    (lab / "windows" / "prior.img").write_text("old")
    monkeypatch.setattr(bootstrap, "_lab",
                        lambda: (lab, {"status": "running", "container_id": "a" * 64}))
    monkeypatch.setattr(bootstrap.re_docker_lab, "inspect",
                        lambda system: {"status": "running", "container_id": "b" * 64})
    stage = lab / "oracle" / "oem"
    stage.mkdir(parents=True)
    monkeypatch.setattr(bootstrap, "prepare",
                        lambda: {"oem": str(stage), "hosts": "test-hosts",
                                 "manifest_sha256": "d" * 64})
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        if argv[:3] == ["docker", "image", "inspect"]:
            return "sha256:" + "c" * 64
        return ""
    monkeypatch.setattr(bootstrap, "_run", run)
    result = bootstrap.bootstrap(recreate=True, yes=True, accept_license=True)
    assert result["oracle_ready"] is False
    assert not (lab / "windows" / "prior.img").exists()
    assert calls.index(["docker", "stop", "-t", "120", "b" * 64]) > calls.index(
        ["docker", "image", "inspect", "--format", "{{.Id}}", "dockurr/windows"])
    creates = [args for args in calls if args[:2] == ["docker", "create"]]
    assert len(creates) == 1
    assert "127.0.0.1:2222:22" in creates[0]
    assert "USER_PORTS=22" in creates[0]
    assert any(arg.startswith("type=bind,src=") and arg.endswith(",dst=/oem,readonly")
               for arg in creates[0])
    assert creates[0][-1] == "sha256:" + "c" * 64


def test_bootstrap_fails_preflight_before_reset(tmp_path, monkeypatch):
    lab = tmp_path / "re-lab"
    (lab / "windows").mkdir(parents=True)
    (lab / "workspace").mkdir()
    (lab / ".lock").touch()
    disk = lab / "windows" / "disk.img"
    disk.write_bytes(b"Windows stays")
    (lab / "windows.env").write_text("VERSION=11\n")
    (lab / "windows.env").chmod(0o600)
    monkeypatch.setattr(bootstrap, "_lab",
                        lambda: (lab, {"container_id": "a" * 64}))
    monkeypatch.setattr(bootstrap.re_docker_lab, "inspect",
                        lambda system: {"container_id": "b" * 64})
    monkeypatch.setattr(bootstrap, "prepare",
                        lambda: (_ for _ in ()).throw(VMError("compiler missing")))
    with pytest.raises(VMError, match="compiler missing"):
        bootstrap.bootstrap(recreate=True, yes=True, accept_license=True)
    assert disk.read_bytes() == b"Windows stays"


def test_oracle_bootstrap_cli_requires_gate_flags():
    from kitt.cli.main import build_parser
    args = build_parser().parse_args(["re", "oracle", "bootstrap",
                                     "--recreate", "--yes", "--accept-license"])
    assert args.re_action == "oracle" and args.oracle_action == "bootstrap"
    assert args.recreate and args.yes and args.accept_license


def test_pilot_never_executes_without_explicit_operator_approval(monkeypatch):
    from kitt import re_oracle_pilot
    monkeypatch.setattr(re_oracle_pilot.re_oracle, "doctor",
                        lambda **kwargs: (_ for _ in ()).throw(AssertionError("Unexpected network call")))
    with pytest.raises(VMError, match="--yes"):
        re_oracle_pilot.pilot(manifest_sha256="a" * 64)


def test_pilot_is_blocked_without_authenticated_windows(monkeypatch):
    from kitt import re_oracle_pilot
    monkeypatch.setattr(re_oracle_pilot.re_oracle, "doctor",
                        lambda **kwargs: {"oracle_ready": False, "reason": "SSH unavailable"})
    result = re_oracle_pilot.pilot(manifest_sha256="a" * 64, yes=True)
    assert result["status"] == "BLOCKED_ENV"


def test_fixed_pilot_compares_two_receipts_without_arbitrary_shell(tmp_path, monkeypatch):
    from kitt import re_oracle_pilot
    lab = tmp_path / "re-lab"
    private = lab / "oracle" / "oem"
    private.mkdir(parents=True)
    workspace = lab / "workspace" / ".kitt-oracle"
    workspace.mkdir(parents=True)
    binary = b"MZ" + b"safe-fixture" * 20
    (workspace / "event_probe.exe").write_bytes(binary)
    expected = hashlib.sha256(binary).hexdigest()
    (private / "manifest.json").write_text(json.dumps({
        "probes": {"event_auto_reset": {"path": "event_probe.exe", "sha256": expected}}
    }))
    digest = hashlib.sha256((private / "manifest.json").read_bytes()).hexdigest()
    monkeypatch.setattr(re_oracle_pilot.re_oracle, "doctor",
                        lambda **kwargs: {"oracle_ready": True})
    monkeypatch.setattr(re_oracle_pilot.re_oracle_bootstrap, "_lab",
                        lambda: (lab, {"container_id": "a" * 64}))
    monkeypatch.setattr(re_oracle_pilot, "_hosts",
                        lambda path: {"reference": "kitt-win-oracle", "candidate": "wine"})
    observation = '{"before":258,"signaled":0,"consumed":258}\n'
    ref = {"schema_version": 1, "probe": "event_auto_reset", "probe_sha256": expected,
           "platform": "Windows", "release": "11", "exit_code": 0,
           "timed_out": False, "truncated": False, "duration_ms": 1.5,
           "stdout": observation, "stderr": ""}
    ref["sha256"] = hashlib.sha256(
        json.dumps(ref, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    monkeypatch.setattr(re_oracle_pilot, "_ssh_request",
                        lambda config, alias, name: ref)
    calls = []
    def fixed_isolated_wine(lab, linux, binary):
        calls.append((lab, linux, binary))
        return 0, observation.encode(), b""
    monkeypatch.setattr(re_oracle_pilot, "_run_isolated_wine", fixed_isolated_wine)
    result = re_oracle_pilot.pilot(manifest_sha256=digest, yes=True)
    assert result["status"] == "PASS_SCENARIO"
    assert result["toolbox_vm_isolated"] is True
    assert result["candidate"]["environment"] == "isolated-ephemeral-wine-container"
    assert result["source_binary_sha256"] == expected
    assert calls and calls[0][2] == workspace / "event_probe.exe"
    assert (lab / "oracle" / "pilot.json").is_file()


def test_native_image_lock_mismatch_never_removes_existing_disk(tmp_path, monkeypatch):
    """Reject a retagged Dockur image before stopping or deleting anything."""
    lab = tmp_path / "re-lab"
    (lab / "windows").mkdir(parents=True)
    (lab / "oracle").mkdir()
    disk = lab / "windows" / "disk.img"
    disk.write_bytes(b"preserve")
    (lab / ".lock").touch()
    credentials = lab / "windows.env"
    credentials.write_text("VERSION=11\n")
    credentials.chmod(0o600)
    (lab / "oracle" / "image.lock.json").write_text(json.dumps({
        "image_id": "sha256:" + "a" * 64,
        "repo_digest": "dockurr/windows@sha256:" + "b" * 64,
    }))
    (lab / "oracle" / "image.lock.json").chmod(0o600)
    monkeypatch.setattr(bootstrap, "_lab", lambda: (lab, {"container_id": "a" * 64}))
    monkeypatch.setattr(bootstrap.re_docker_lab, "inspect",
                        lambda role: {"container_id": "c" * 64})
    monkeypatch.setattr(bootstrap, "prepare",
                        lambda: {"oem": str(lab / "oracle"), "hosts": "unused",
                                 "manifest_sha256": "d" * 64})
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        if argv[:3] == ["docker", "image", "inspect"]:
            return "sha256:" + "e" * 64
        raise AssertionError("No Docker mutation allowed after image mismatch")
    monkeypatch.setattr(bootstrap, "_run", run)
    with pytest.raises(VMError, match="image identity changed"):
        bootstrap.bootstrap(recreate=True, yes=True, accept_license=True)
    assert disk.read_bytes() == b"preserve"
    assert calls and all(argv[:2] != ["docker", "stop"] for argv in calls)
