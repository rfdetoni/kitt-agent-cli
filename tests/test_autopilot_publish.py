"""Unattended publishing never mutates GitHub without authorization and test proof."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from kitt.runtime.autopilot_publish import (
    GithubPublisher, _safe_path, publish_verified,
)


def test_sensitive_paths_are_never_uploaded():
    for value in (".env", ".env.prod", ".git/config", ".kitt/index.db",
                  "node_modules/x", "../private", "/etc/passwd",
                  "secrets/x.pem"):
        assert not _safe_path(value)
    assert _safe_path("src/main/java/App.java")


def test_publisher_rejects_remote_divergence_before_writes():
    publisher = GithubPublisher("rfdetoni/sample", "test-token")
    publisher.api = Mock(return_value={"object": {"sha": "a" * 40}})
    with pytest.raises(RuntimeError, match="diverged"):
        publisher.publish(
            {"head": "b" * 40, "files": {}, "tracked": set()},
            "main", "test",
        )
    publisher.api.assert_called_once_with("GET", "/git/ref/heads/main")


def test_publish_requires_matching_post_test_snapshot(monkeypatch, tmp_path: Path):
    state = SimpleNamespace(
        get=lambda name: {"ok": True, "publication_snapshot": {
            "digest": "verified", "head": "a" * 40,
        }} if name == "autopilot.task.verification" else None,
    )
    runtime = SimpleNamespace(root=tmp_path, state=state)
    monkeypatch.setenv("KITT_AUTOPILOT_PUBLISH_ENABLED", "true")
    with patch("kitt.runtime.autopilot_publish.inventory", return_value={
        "head": "a" * 40, "digest": "modified", "files": {}, "tracked": set(),
    }):
        with pytest.raises(PermissionError, match="changed since validation"):
            publish_verified(runtime, {"message": "ship"}, allow_network=True)


def test_publish_refuses_without_operator_enable(monkeypatch, tmp_path):
    monkeypatch.delenv("KITT_AUTOPILOT_PUBLISH_ENABLED", raising=False)
    runtime = SimpleNamespace(root=tmp_path, state=SimpleNamespace(get=Mock()))
    with pytest.raises(PermissionError, match="not operator enabled"):
        publish_verified(runtime, {"message": "ship"}, allow_network=True)


def test_non_fast_forward_guard_is_explicit():
    publisher = GithubPublisher("rfdetoni/sample", "test-token")
    api = Mock(side_effect=[
        {"object": {"sha": "a" * 40}},
        {"tree": {"sha": "b" * 40}},
        {"tree": [{"path": "src/x.py", "type": "blob",
                   "sha": "c" * 40, "mode": "100644"}]},
        {"sha": "d" * 40},
        {"sha": "e" * 40},
        {"sha": "f" * 40},
        {},
    ])
    publisher.api = api
    manifest = {
        "head": "a" * 40,
        "files": {"src/x.py": {"mode": "100644", "sha": "f" * 40,
                               "data": b"print('ok')"}},
        "tracked": {"src/x.py"},
    }
    result = publisher.publish(manifest, "main", "verified work")
    assert result["published"] is True
    assert api.call_args.args[:2] == ("PATCH", "/git/refs/heads/main")
    assert api.call_args.args[2]["force"] is False
