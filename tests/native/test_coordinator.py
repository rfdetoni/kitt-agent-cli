from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from kitt.history.database import HistoryDatabase
from kitt.native.coordinator import (
    CoordinationConflict,
    LeaseRequest,
    WorkspaceCoordinator,
)
from kitt.native.storage import NativeStateRepository


class Db:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
    @contextmanager
    def get_connection(self):
        try:
            yield self.conn; self.conn.commit()
        except Exception:
            self.conn.rollback(); raise


def test_read_read_allowed_write_conflicts(tmp_path: Path):
    db = Db(); NativeStateRepository(db, "ws")
    c = WorkspaceCoordinator(str(tmp_path), str(tmp_path), db, "ws")
    c.acquire("symbol:a", "child-a", "READ", "inspect")
    c.acquire("symbol:a", "child-b", "READ", "inspect")
    with pytest.raises(CoordinationConflict):
        c.acquire("symbol:a", "child-c", "WRITE", "edit")
    c.release_owner("child-a"); c.release_owner("child-b")
    assert c.acquire("symbol:a", "child-c", "WRITE", "edit").mode == "WRITE"


def test_non_git_workspace_falls_back_without_failing(tmp_path: Path):
    db = Db(); NativeStateRepository(db, "ws")
    c = WorkspaceCoordinator(str(tmp_path), str(tmp_path), db, "ws")
    state = c.prepare_child("child-1")
    assert state.state == "SHARED_FALLBACK"
    assert state.path == str(tmp_path.resolve())


def _git(cwd: Path, *args: str):
    import subprocess
    return subprocess.run(["git", *args], cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)


def test_git_child_worktree_integrates(tmp_path: Path):
    import shutil
    if shutil.which("git") is None:
        pytest.skip("git unavailable")
    repo = tmp_path / "repo"; repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "kitt-test@example.invalid")
    _git(repo, "config", "user.name", "KITT Test")
    (repo / ".gitignore").write_text(".kitt/\n", encoding="utf-8")
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", "a.txt"); _git(repo, "commit", "-m", "base")

    db = Db(); NativeStateRepository(db, "ws")
    c = WorkspaceCoordinator(str(repo), str(repo), db, "ws")
    state = c.prepare_child("child-merge")
    assert state.state == "READY"
    worktree = Path(state.path)
    (worktree / "a.txt").write_text("two\n", encoding="utf-8")
    merged = c.integrate_child("child-merge")
    assert merged.state == "MERGED"
    assert (repo / "a.txt").read_text(encoding="utf-8") == "two\n"


def test_dirty_main_preserves_child_worktree(tmp_path: Path):
    import shutil
    if shutil.which("git") is None:
        pytest.skip("git unavailable")
    repo = tmp_path / "repo"; repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "kitt-test@example.invalid")
    _git(repo, "config", "user.name", "KITT Test")
    (repo / ".gitignore").write_text(".kitt/\n", encoding="utf-8")
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", "a.txt"); _git(repo, "commit", "-m", "base")
    db = Db(); NativeStateRepository(db, "ws")
    c = WorkspaceCoordinator(str(repo), str(repo), db, "ws")
    state = c.prepare_child("child-conflict")
    worktree = Path(state.path)
    (worktree / "a.txt").write_text("child\n", encoding="utf-8")
    (repo / "a.txt").write_text("dirty-main\n", encoding="utf-8")
    with pytest.raises(CoordinationConflict):
        c.integrate_child("child-conflict")
    assert worktree.exists()
    assert (worktree / "a.txt").read_text(encoding="utf-8") == "child\n"



def test_batch_claim_is_atomic_and_path_ancestors_conflict(tmp_path: Path):
    db = Db()
    NativeStateRepository(db, "ws")
    coordinator = WorkspaceCoordinator(str(tmp_path), str(tmp_path), db, "ws")
    coordinator.acquire("path:src", "child-a", "WRITE", "owns source tree")

    with pytest.raises(CoordinationConflict):
        coordinator.acquire_many(
            [
                LeaseRequest("path:src/service.py", "WRITE", "edit service"),
                LeaseRequest("path:docs/readme.md", "WRITE", "edit docs"),
            ],
            "child-b",
        )

    with db.get_connection() as conn:
        rows = conn.execute(
            "SELECT resource_id FROM coordination_leases WHERE owner_id='child-b'"
        ).fetchall()
    assert rows == []


def test_waiting_mutation_acquires_after_owner_releases(tmp_path: Path):
    root = tmp_path / "state"
    root.mkdir()
    db = HistoryDatabase(str(root))
    coordinator = WorkspaceCoordinator(str(root), str(root), db, "ws")
    coordinator.acquire("path:src", "child-a", "WRITE", "first writer")

    acquired = []
    failed = []

    def wait_for_path():
        try:
            grant = coordinator.acquire(
                "path:src/service.py",
                "child-b",
                "WRITE",
                "second writer",
                wait_timeout=2.0,
            )
            acquired.append(grant)
        except Exception as exc:
            failed.append(exc)

    worker = threading.Thread(target=wait_for_path)
    worker.start()
    time.sleep(0.15)
    coordinator.release_owner("child-a")
    worker.join(timeout=3.0)

    assert not worker.is_alive()
    assert failed == []
    assert acquired and acquired[0].owner_id == "child-b"
    coordinator.release_owner("child-b")
    db.close()
