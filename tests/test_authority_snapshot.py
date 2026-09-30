from __future__ import annotations

from types import SimpleNamespace

import pytest

from kitt.security.authority_snapshot import (
    capture_authority_snapshot,
    validate_authority_snapshot,
)
from kitt.security.context import ExecutionSecurityContext


class Approval:
    remembered_rules = []


def test_authority_snapshot_is_immutable_and_scope_bound():
    context = ExecutionSecurityContext.create_user_context(
        "ws",
        "conv",
        "turn",
        capabilities={"repo.read", "repo.write", "browser.read"},
    )
    policy = SimpleNamespace(root_path="/tmp/ws", DISALLOWED_PROCESS_EXECUTABLES={"bash"})
    autonomy = SimpleNamespace(to_dict=lambda: {"level": "supervised"})
    snap = capture_authority_snapshot(
        context,
        policy=policy,
        autonomy=autonomy,
        approval_manager=Approval(),
        sandbox_profile="workspace-write",
    )
    validate_authority_snapshot(snap, context)

    tampered = {"snapshot": dict(snap["snapshot"]), "digest": snap["digest"]}
    tampered["snapshot"]["workspace_id"] = "other"
    with pytest.raises(PermissionError):
        validate_authority_snapshot(tampered, context)
