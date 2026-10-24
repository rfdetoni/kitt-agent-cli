"""Dedicated RE image provisioning: no actual Docker daemon in unit tests."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from kitt import re_image_install
from kitt.cli.main import build_parser
from kitt.vm_supervisor import VMError


def test_parser_images_prepare_and_status():
    for action in ("status", "prepare"):
        args = build_parser().parse_args(["re", "lab", "images", action])
        assert args.lab_action == "images"
        assert args.image_action == action
        assert args.runtime == "docker"


def test_re_tools_status_is_read_only_and_describes_missing(monkeypatch):
    monkeypatch.setattr(re_image_install.vm_container, "_runtime",
                        lambda runtime: "/usr/bin/docker")
    monkeypatch.setattr(re_image_install.vm_container, "_image_id",
                        lambda docker, tag: (_ for _ in ()).throw(VMError("missing")))
    assert re_image_install.status()["status"] == "MISSING_IMAGES"
    assert set(re_image_install.status()["images"]) == {"full", "ghidra", "frida"}


def test_re_tools_status_handles_unavailable_docker_without_mutating(monkeypatch):
    monkeypatch.setattr(re_image_install.vm_container, "_runtime",
                        lambda runtime: (_ for _ in ()).throw(VMError("socket unavailable")))
    state = re_image_install.status()
    assert state["status"] == "BLOCKED_ENV"
    assert "socket unavailable" in state["reason"]
    assert "sandbox" in state["hint"]


def test_re_tools_prepare_reuses_all_local_images_with_offline_smoke(monkeypatch):
    monkeypatch.setattr(re_image_install.vm_container, "_runtime",
                        lambda runtime: "/usr/bin/docker")
    monkeypatch.setattr(re_image_install.vm_container, "_image_id",
                        lambda binary, image: "sha256:" + "a" * 64)
    monkeypatch.setattr(re_image_install.vm_container, "pull_image",
                        lambda **kw: (_ for _ in ()).throw(AssertionError("pull not needed")))
    monkeypatch.setattr(re_image_install.vm_container, "build_image",
                        lambda **kw: (_ for _ in ()).throw(AssertionError("build not needed")))
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(re_image_install.subprocess, "run", fake_run)
    result = re_image_install.prepare()
    assert result["status"] == "READY"
    assert all(x["source"] == "existing" for x in result["images"].values())
    assert len(calls) == 3
    for argv in calls:
        assert "none" in argv and "ALL" in argv
        assert "--read-only" in argv
        assert "--privileged" not in argv
        assert "unix:///var/run/docker.sock" not in " ".join(argv)
        assert "/var/run/docker.sock" not in " ".join(argv)


def test_re_tools_prepare_pulls_or_builds_missing_images(monkeypatch):
    monkeypatch.setattr(re_image_install.vm_container, "_runtime",
                        lambda runtime: "/usr/bin/docker")
    available = set()
    pulled, built = [], []

    def image_id(binary, tag):
        if tag not in available:
            raise VMError("missing")
        return "sha256:" + "a" * 64

    def pull(**kw):
        pulled.append(kw)
        if kw["image"] == "ghidra":
            raise VMError("GHCR temporarily unavailable")
        profile = ("full" if kw["image"] == "toolbox" else kw["image"])
        available.add(re_image_install.vm_container.IMAGES[profile])

    def build(**kw):
        built.append(kw)
        available.add(re_image_install.vm_container.IMAGES[kw["image"]])

    monkeypatch.setattr(re_image_install.vm_container, "_image_id", image_id)
    monkeypatch.setattr(re_image_install.vm_container, "pull_image", pull)
    monkeypatch.setattr(re_image_install.vm_container, "build_image", build)
    monkeypatch.setattr(re_image_install.subprocess, "run",
                        lambda *a, **kw: SimpleNamespace(returncode=0))

    result = re_image_install.prepare()
    assert set(result["images"]) == {"full", "ghidra", "frida"}
    assert result["images"]["ghidra"]["source"] == "built"
    assert result["images"]["frida"]["source"] == "pulled"
    assert result["images"]["full"]["source"] == "pulled"
    assert len(pulled) == 3 and len(built) == 1
    assert result["windows_guest_unchanged"] is True


def test_failed_image_smoke_fails_installer(monkeypatch):
    monkeypatch.setattr(re_image_install.vm_container, "_runtime",
                        lambda runtime: "/usr/bin/docker")
    monkeypatch.setattr(re_image_install.vm_container, "_image_id",
                        lambda binary, image: "sha256:" + "b" * 64)
    monkeypatch.setattr(re_image_install.subprocess, "run",
                        lambda *a, **kw: SimpleNamespace(returncode=42))
    with pytest.raises(VMError, match="smoke check failed"):
        re_image_install.prepare()
