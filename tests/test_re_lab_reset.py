"""Scoped lab reset must be bound to immutable resource IDs and private operator key."""
import os
import stat
from pathlib import Path

import pytest

from kitt import re_lab_reset as reset
from kitt.vm_supervisor import VMError


def managed_lab(tmp_path, monkeypatch):
    root = tmp_path / "re-lab"
    (root / "oracle").mkdir(parents=True)
    (root / "windows").mkdir(mode=0o700)
    (root / "workspace").mkdir(mode=0o700)
    (root / ".lock").touch()
    key = root / "oracle" / "client_ed25519"
    key.write_text("fixed test-only random key")
    key.chmod(0o600)
    monkeypatch.setattr(reset.re_lab, "_root", lambda path: root)
    monkeypatch.setattr(reset.re_docker_lab, "_lab_root", lambda path: root)
    identities = {"windows": "a" * 64, "linux": "b" * 64}
    def inspect(role):
        return {"container_id": identities[role], "status": "exited",
                "workspace": str(root / "workspace")}
    monkeypatch.setattr(reset.re_docker_lab, "inspect", inspect)
    return root, identities


def test_plan_does_not_mutate_and_token_binds_scope(tmp_path, monkeypatch):
    root, identities = managed_lab(tmp_path, monkeypatch)
    (root / "windows" / "disk.img").write_bytes(b"safe")
    before = set(root.rglob("*"))
    first = reset.plan(scope="windows", destroy_data=True)
    assert first["inventory"]["containers"] == {"windows": "a" * 64}
    assert set(root.rglob("*")) == before
    with pytest.raises(VMError, match="identity or paths"):
        reset.recreate(scope="all", destroy_data=True, confirm=first["confirm_token"],
                       accept_license=True)
    assert (root / "windows" / "disk.img").read_bytes() == b"safe"


def test_refuse_without_destructive_gate(tmp_path, monkeypatch):
    root, _ = managed_lab(tmp_path, monkeypatch)
    with pytest.raises(VMError, match="destroy-data"):
        reset.plan(scope="windows")
    with pytest.raises(VMError, match="destroy-data"):
        reset.recreate(scope="windows", destroy_data=False, confirm="garbage")
    with pytest.raises(VMError, match="accept-license"):
        reset.recreate(scope="windows", destroy_data=True, confirm="garbage")


def test_reset_token_rejects_changed_container_identity(tmp_path, monkeypatch):
    root, identities = managed_lab(tmp_path, monkeypatch)
    token = reset.plan(scope="windows", destroy_data=True)["confirm_token"]
    identities["windows"] = "c" * 64
    with pytest.raises(VMError, match="identity or paths"):
        reset.recreate(scope="windows", destroy_data=True, confirm=token,
                       accept_license=True)


def test_reset_rejects_nested_symlink(tmp_path, monkeypatch):
    root, identities = managed_lab(tmp_path, monkeypatch)
    victim = tmp_path / "other.txt"
    victim.write_text("keep")
    (root / "windows" / "unsafe").symlink_to(victim)
    with pytest.raises(VMError, match="link"):
        reset.plan(scope="windows", destroy_data=True)
    assert victim.read_text() == "keep"


def test_reset_cli_requires_explicit_scope_and_token():
    from kitt.cli.main import build_parser
    args = build_parser().parse_args([
        "re", "lab", "recreate", "--scope", "windows", "--destroy-data",
        "--confirm", "token", "--accept-license",
    ])
    assert args.re_action == "lab" and args.lab_action == "recreate"
    assert args.destroy_data and args.accept_license and args.confirm == "token"
