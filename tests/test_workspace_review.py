"""Review/apply/rewind preserves content integrity and principal path scope."""
from types import SimpleNamespace

import pytest

from kitt.runtime.workspace_review import WorkspaceReview
from kitt.runtime.core_runtime import OPERATION_REGISTRY
from kitt.security.capabilities import CAP_REPO_READ, CAP_REPO_WRITE


class State:
    def __init__(self):
        self.data = {}
    def set(self, key, value, ttl_seconds=None):
        self.data[key] = value
    def get(self, key):
        return self.data.get(key)
    def compare_and_swap_status(self, key, expected, next_value):
        current = self.data.get(key)
        if not current or current["status"] != expected:
            return False
        self.data[key] = next_value
        return True


def test_review_previews_applies_and_rewinds(tmp_path):
    file = tmp_path / "hello.txt"
    file.write_text("old\n", encoding="utf-8")
    svc = WorkspaceReview(SimpleNamespace(root=tmp_path, state=State()))
    draft = svc.preview({"files": [{"path": "hello.txt", "content": "new\n"}]}, None)
    assert file.read_text() == "old\n"
    assert "-old" in draft["changes"][0]["diff"]
    ident = draft["review_id"]
    assert svc.apply({"review_id": ident}, None)["status"] == "APPLIED"
    assert file.read_text() == "new\n"
    assert svc.rewind({"review_id": ident}, None)["status"] == "REWOUND"
    assert file.read_text() == "old\n"


def test_review_rejects_tampering_and_control_plane(tmp_path):
    svc = WorkspaceReview(SimpleNamespace(root=tmp_path, state=State()))
    with pytest.raises(PermissionError):
        svc.preview({"files": [{"path": ".git/config", "content": "oops"}]}, None)
    with pytest.raises(PermissionError):
        svc.preview({"files": [{"path": ".kitt/config", "content": "oops"}]}, None)
    file = tmp_path / "file.txt"
    file.write_text("old")
    ident = svc.preview({"files": [{"path": "file.txt", "content": "new"}]}, None)["review_id"]
    file.write_text("external")
    with pytest.raises(ValueError, match="conflict"):
        svc.apply({"review_id": ident}, None)
    assert file.read_text() == "external"


def test_review_undo_detects_post_apply_conflicts(tmp_path):
    svc = WorkspaceReview(SimpleNamespace(root=tmp_path, state=State()))
    ident = svc.preview({"files": [{"path": "created.txt", "content": "new"}]}, None)["review_id"]
    svc.apply({"review_id": ident}, None)
    (tmp_path / "created.txt").write_text("someone else")
    with pytest.raises(ValueError, match="conflict"):
        svc.rewind({"review_id": ident}, None)
    assert (tmp_path / "created.txt").read_text() == "someone else"


def test_review_path_scope_and_authority(tmp_path):
    svc = WorkspaceReview(SimpleNamespace(root=tmp_path, state=State()))
    restricted = SimpleNamespace(assert_path_allowed=lambda path: (
        None if path == "allowed.txt" else (_ for _ in ()).throw(PermissionError(path))
    ))
    with pytest.raises(PermissionError):
        svc.preview({"files": [{"path": "hidden.txt", "content": "secret"}]}, restricted)
    assert OPERATION_REGISTRY["review.preview"].required_capability == CAP_REPO_READ
    assert OPERATION_REGISTRY["review.apply"].required_capability == CAP_REPO_WRITE
    assert OPERATION_REGISTRY["review.rewind"].sensitive is True

def test_review_durable_state_failure_restores_preimage(tmp_path):
    from kitt.runtime.workspace_review import WorkspaceReview
    class FailingState(State):
        def compare_and_swap_status(self, key, expected, next_value):
            if next_value["status"] == "APPLIED":
                raise RuntimeError("sqlite unavailable")
            return super().compare_and_swap_status(key, expected, next_value)
    file = tmp_path / "text.txt"
    file.write_text("before")
    service = WorkspaceReview(SimpleNamespace(root=tmp_path, state=FailingState()))
    ident = service.preview({"files": [{"path": "text.txt", "content": "after"}]}, None)["review_id"]
    with pytest.raises(RuntimeError, match="sqlite unavailable"):
        service.apply({"review_id": ident}, None)
    assert file.read_text() == "before"

def test_review_owner_isolation(tmp_path):
    svc = WorkspaceReview(SimpleNamespace(root=tmp_path, state=State()))
    def principal(ident):
        return SimpleNamespace(principal_type="child", principal_id=ident,
            workspace_id="w", conversation_id="c", assert_path_allowed=lambda path: None)
    rid = svc.preview({"files":[{"path":"a.txt","content":"ok"}]},principal("one"))["review_id"]
    with pytest.raises(PermissionError, match="another execution principal"):
        svc.apply({"review_id":rid},principal("two"))
    assert not (tmp_path/"a.txt").exists()

def test_review_only_one_concurrent_apply_succeeds(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    svc = WorkspaceReview(SimpleNamespace(root=tmp_path, state=State()))
    rid = svc.preview({"files": [{"path": "file.txt", "content": "content"}]}, None)["review_id"]
    def apply(_):
        try:
            return svc.apply({"review_id": rid}, None)["status"]
        except ValueError:
            return "CONFLICT"
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(apply, range(2)))
    assert outcomes == ["APPLIED", "CONFLICT"]
    assert (tmp_path / "file.txt").read_text() == "content"
