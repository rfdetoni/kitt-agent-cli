"""Autopilot checkpoint conflicts and real validation evidence."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kitt.runtime.autopilot_tasks import AutopilotTaskService


class State:
    def __init__(self):
        self.d = {}

    def get(self, k):
        return self.d.get(k)

    def set(self, k, v):
        self.d[k] = v

    def delete(self, k):
        self.d.pop(k, None)


def setup_service(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    snap = Mock()
    snap.capture.return_value = SimpleNamespace(
        snapshot_id="snap1", changed_paths=("src",)
    )
    snap.diff.return_value = [{"path": "src", "current_sha256": "after"}]
    snap.restore.return_value = ["src"]
    runtime = SimpleNamespace(
        root=root, conversation_id="conversation", state=State(),
        registry=SimpleNamespace(workspace_snapshot_service=snap),
    )
    return AutopilotTaskService(runtime), runtime, snap


def test_checkpoint_requires_safe_paths_and_rollback_cas(tmp_path):
    service, runtime, snap = setup_service(tmp_path)
    (runtime.root / "src").mkdir()
    checkpoint = service.checkpoint({"paths": ["src"]}, "turn")
    assert checkpoint["snapshot_id"] == "snap1"
    assert service.diff()["expected_current"] == {"src": "after"}
    restored = service.rollback({"expected_current": {"src": "after"}})
    assert restored["restored_paths"] == ["src"]
    assert snap.restore.call_args.kwargs["expected_current"] == {"src": "after"}
    with pytest.raises(PermissionError):
        service.checkpoint({"paths": [".kitt/secrets"]}, "turn")


def test_failed_test_gate_never_records_success(tmp_path, monkeypatch):
    service, runtime, snap = setup_service(tmp_path)
    (runtime.root / "pom.xml").write_text("<project/>")
    runtime.registry.process_runner = SimpleNamespace(
        run=Mock(return_value=SimpleNamespace(
            returncode=1, timed_out=False,
            stdout="", stderr="compiler failed",
        ))
    )
    monkeypatch.delenv("KITT_AUTOPILOT_VALIDATION", raising=False)
    report = service.verify({"network": False}, allow_network=False)
    assert not report["ok"]
    assert "compiler failed" in report["checks"][0]["stderr"]
    assert runtime.state.get(service.VERIFY_KEY) is None
