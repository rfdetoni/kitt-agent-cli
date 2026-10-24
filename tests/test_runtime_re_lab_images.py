"""Model-facing read-only image diagnosis must never receive Docker socket."""
from __future__ import annotations

from types import SimpleNamespace

from kitt import re_image_install
from kitt.runtime.core_runtime import SafeRuntime, OPERATION_REGISTRY
from kitt.security.capabilities import CAP_REPO_READ


def test_re_lab_images_is_read_only_repo_capability(monkeypatch):
    assert OPERATION_REGISTRY["re.lab.images"].required_capability == CAP_REPO_READ
    assert OPERATION_REGISTRY["re.lab.images"].sensitive is False
    runtime = object.__new__(SafeRuntime)
    marker = {"status": "READY", "images": {
        "ghidra": {"status": "READY"}, "frida": {"status": "READY"},
        "full": {"status": "READY"},
    }}
    calls = []
    monkeypatch.setattr(re_image_install, "status",
                        lambda **kw: (calls.append(kw) or marker))
    context = SimpleNamespace(principal_type="agent")
    result = runtime._op_re_lab_images({}, context)
    assert result.success and result.data is marker
    assert calls == [{}]
    assert not runtime._op_re_lab_images({"argv": ["docker", "ps"]}, context).success
    assert not runtime._op_re_lab_images({}, None).success


def test_re_lab_images_exposes_blocker_without_mutating(monkeypatch):
    runtime = object.__new__(SafeRuntime)
    monkeypatch.setattr(re_image_install, "status",
                        lambda **kw: {"status": "BLOCKED_ENV", "images": {},
                                      "reason": "Host Docker unavailable"})
    result = runtime._op_re_lab_images({}, SimpleNamespace(principal_type="agent"))
    assert not result.success
    assert "Docker" in result.data["reason"]
