from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from kitt.artifacts.store import ArtifactStore
from kitt.children.lifecycle import ChildAgentManager
from kitt.children.repository import ChildRepository
from kitt.history.database import HistoryDatabase
from kitt.history.repository import HistoryRepository
from kitt.security.capabilities import CAP_CHILD_SPAWN, CAP_REPO_READ, compute_child_privileges
from kitt.security.context import ExecutionSecurityContext


@pytest.fixture
def children(tmp_path):
    db = HistoryDatabase(str(tmp_path))
    repo = HistoryRepository(db)
    ws = repo.get_or_create_workspace(str(tmp_path))
    conv = repo.create_conversation(ws["id"], "children")["id"]
    artifacts = ArtifactStore(str(tmp_path), db, ephemeral=True)
    manager = ChildAgentManager(
        str(tmp_path), ChildRepository(db), artifacts, workspace_id=ws["id"]
    )
    yield manager, conv, ws["id"]
    manager.close()
    artifacts.close()
    db.close()


def test_leaf_capability_and_identity_enforcement(children):
    manager, conv, ws = children
    assert CAP_CHILD_SPAWN not in compute_child_privileges(
        [CAP_CHILD_SPAWN, CAP_REPO_READ], {CAP_CHILD_SPAWN, CAP_REPO_READ}
    )
    forged = ExecutionSecurityContext(
        ws,
        conv,
        "t",
        "AGENT",
        "CHILD",
        "forged",
        frozenset({CAP_CHILD_SPAWN, CAP_REPO_READ}),
        "trace",
    )
    with pytest.raises(PermissionError, match="Leaf"):
        manager.spawn(conv, "t", task="Create a grandchild", depth=1, security_context=forged)
    assert manager.repo.list(conv) == []


def test_concurrent_admission_does_not_race_rate_or_slot_limits(children):
    manager, conv, _ = children
    release = threading.Event()

    def spawn(_):
        try:
            return manager.spawn(
                conv, "t", task="Inspect", worker=lambda _: release.wait(5) or "done"
            )
        except ValueError:
            return None

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(spawn, range(8)))
        assert sum(r is not None for r in results) == 1
        assert len(manager.repo.list(conv)) == 1
    finally:
        release.set()


@pytest.mark.parametrize("late", ["success", "approval", "failure", "timeout"])
def test_cancelled_child_cannot_be_resurrected(children, late):
    manager, conv, ws = children
    release = threading.Event()
    started = threading.Event()

    def worker(_):
        started.set()
        release.wait(5)
        return "late"

    child = manager.spawn(conv, "t", task="Inspect", worker=worker)
    try:
        assert started.wait(2)
        assert manager.cancel_for_turn(conv, "t") == 1
        if late == "success":
            manager._complete_child(child.id, ws, {"success": True, "output": "late"})
        elif late == "approval":
            manager._mark_waiting_approval(child.id, {"turn_id": "child-turn"})
        else:
            manager._fail_child(
                child.id, TimeoutError("late") if late == "timeout" else RuntimeError("late")
            )
        current = manager.repo.get(child.id)
        assert current.state == "CANCELLED"
        assert current.result_artifact_id is None
    finally:
        release.set()


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), 0, -1])
def test_invalid_timeouts_are_rejected_before_admission(children, timeout):
    manager, conv, _ = children
    with pytest.raises(ValueError, match="finite"):
        manager.spawn(conv, "t", task="Inspect", timeout_seconds=timeout)
    assert manager.repo.list(conv) == []
