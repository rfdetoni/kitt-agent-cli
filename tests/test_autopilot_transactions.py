"""Real worktree lifecycle: edits and abandonment leave base untouched."""
from types import SimpleNamespace

import pytest

from kitt.runtime.autopilot_publish import _git
from kitt.runtime.autopilot_transactions import TaskWorktree


class MemoryState:
    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value):
        self.data[key] = value

    def delete(self, key):
        self.data.pop(key, None)


@pytest.fixture
def task(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "file.txt").write_text("original")
    _git(root, "add", "file.txt")
    _git(root, "-c", "user.name=CI", "-c", "user.email=ci@example.invalid",
         "commit", "-qm", "baseline")
    return TaskWorktree(SimpleNamespace(root=root, state=MemoryState()))


def test_worktree_edits_do_not_reach_original(task):
    record = task.start({})
    isolated = task.directory()
    assert isolated != task.root
    (isolated / "file.txt").write_text("changed")
    assert task.status({})["modified"]
    assert (task.root / "file.txt").read_text() == "original"
    assert task.abandon({})["abandoned"] is True
    assert task.current() is None
    assert isolated.exists()
    assert task.start({})["path"] != record["path"]


def test_dirty_checkout_denied(task):
    (task.root / "file.txt").write_text("dirty")
    with pytest.raises(PermissionError):
        task.start({})


def test_stateless_runtime_has_no_active_transaction(tmp_path):
    task = TaskWorktree(SimpleNamespace(root=tmp_path, state=None))
    assert task.current() is None
    with pytest.raises(RuntimeError, match="durable"):
        task.start({})
