from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from kitt.compaction.service import CompactionService
from kitt.core.turn_tool_loop import (
    _completion_recovery_exhausted,
    _observational_fingerprint,
    _stuck_sequence,
)
from kitt.history.database import HistoryDatabase
from kitt.history.repository import HistoryRepository
from kitt.history.session_tree import SessionTreeRepository
from kitt.native.coordinator import WorkspaceCoordinator
from kitt.runtime.process_lifecycle import ManagedProcessManager
from kitt.security.capabilities import CAP_PROCESS_RUN
from kitt.security.context import ExecutionSecurityContext
from kitt.tools.registry import ToolRegistry


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return completed.stdout.strip()


@pytest.mark.skipif(os.name == "nt", reason="POSIX signal semantics are exercised here")
def test_managed_process_control_uses_original_authority_snapshot(tmp_path: Path):
    registry = ToolRegistry(root_dir=str(tmp_path))
    manager = ManagedProcessManager(
        registry.process_runner,
        registry,
        workspace_id="ws-authority",
    )
    original = ExecutionSecurityContext.create_user_context(
        workspace_id="ws-authority",
        conversation_id="conv-authority",
        turn_id="turn-origin",
        capabilities={CAP_PROCESS_RUN},
    )
    try:
        started = manager.start(
            argv=[
                sys.executable,
                "-u",
                "-c",
                (
                    "import sys,time; "
                    "print('ready', flush=True); "
                    "line=sys.stdin.readline(); "
                    "print(line.strip(), flush=True); "
                    "time.sleep(30)"
                ),
            ],
            conversation_id="conv-authority",
            turn_id="turn-origin",
            security_context=original,
            sandbox_profile="full-access",
        )
        process_id = started["process_id"]

        weakened = replace(
            original,
            turn_id="turn-later",
            capabilities=frozenset(),
        )
        with pytest.raises(PermissionError, match="capability mismatch"):
            manager.stdin(
                process_id,
                "must-not-write\n",
                security_context=weakened,
                turn_id="turn-later",
            )
        with pytest.raises(PermissionError, match="capability mismatch"):
            manager.signal(
                process_id,
                "TERM",
                security_context=weakened,
                turn_id="turn-later",
            )

        later_same_authority = original.with_turn("turn-later")
        written = manager.stdin(
            process_id,
            "accepted\n",
            security_context=later_same_authority,
            turn_id="turn-later",
        )
        assert written["bytes_written"] == len(b"accepted\n")

        signalled = manager.signal(
            process_id,
            "TERM",
            security_context=later_same_authority,
            turn_id="turn-later",
        )
        assert signalled["signal"] == "TERM"
    finally:
        manager.close()
        registry.close()


def test_git_worktrees_isolate_integrate_discard_and_cancel(tmp_path: Path):
    repo_root = tmp_path / "repo"
    state_root = tmp_path / "state"
    repo_root.mkdir()
    state_root.mkdir()
    _git(repo_root, "init")
    _git(repo_root, "config", "user.name", "KITT Acceptance")
    _git(repo_root, "config", "user.email", "kitt-acceptance@local.invalid")
    (repo_root / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repo_root, "add", "base.txt")
    _git(repo_root, "commit", "-m", "base")

    db = HistoryDatabase(str(state_root))
    coordinator = WorkspaceCoordinator(
        str(repo_root),
        str(state_root),
        db,
        "ws-worktree",
    )
    try:
        child_a = coordinator.prepare_child("child-a")
        child_b = coordinator.prepare_child("child-b")
        child_a_root = Path(child_a.path)
        child_b_root = Path(child_b.path)

        (child_a_root / "child-a.txt").write_text("from-a\n", encoding="utf-8")
        assert not (repo_root / "child-a.txt").exists()
        assert not (child_b_root / "child-a.txt").exists()

        merged = coordinator.integrate_child("child-a")
        assert merged.state == "MERGED"
        assert (repo_root / "child-a.txt").read_text(encoding="utf-8") == "from-a\n"
        assert not (child_b_root / "child-a.txt").exists()

        discard = coordinator.prepare_child("child-discard")
        discard_root = Path(discard.path)
        (discard_root / "discarded.txt").write_text("discard\n", encoding="utf-8")
        coordinator.discard_isolated_workspace("child-discard")
        assert not discard_root.exists()
        assert not (repo_root / "discarded.txt").exists()

        cancelled = coordinator.prepare_child("child-cancelled")
        cancelled_root = Path(cancelled.path)
        (cancelled_root / "cancelled.txt").write_text("cancelled\n", encoding="utf-8")
        coordinator.abandon_child("child-cancelled", preserve_worktree=True)
        assert cancelled_root.exists()
        assert not (repo_root / "cancelled.txt").exists()
        coordinator.discard_isolated_workspace("child-cancelled")
        assert not cancelled_root.exists()

        coordinator.discard_isolated_workspace("child-b")
        assert not child_b_root.exists()
    finally:
        db.close()


def test_runtime_compaction_preserves_critical_execution_evidence(tmp_path: Path):
    db = HistoryDatabase(str(tmp_path))
    history = HistoryRepository(db)
    workspace = history.get_or_create_workspace(str(tmp_path))
    conversation = history.create_conversation(workspace["id"], "compaction")
    tree = SessionTreeRepository(db)
    critical = [
        "Objective: repair the failing build",
        "command: pytest -q tests/test_checkout.py",
        "stderr: AssertionError: checkout mismatch",
        "exit code 17",
        "Affected path src/checkout/service.py",
        "Constraint: must not change the public wire contract",
        "Validation failed: checkout regression remains open",
    ]
    try:
        for value in critical:
            tree.append_entry(conversation["id"], "MESSAGE", {"content": value})
        for index in range(20):
            tree.append_entry(
                conversation["id"],
                "MESSAGE",
                {"content": f"irrelevant verbose context line {index}"},
            )
        tree.append_entry(
            conversation["id"],
            "MESSAGE",
            {"content": "recent message one"},
        )
        tree.append_entry(
            conversation["id"],
            "MESSAGE",
            {"content": "recent message two"},
        )

        service = CompactionService(
            db,
            tree,
            summarizer=lambda _raw: "Short narrative that omits diagnostics.",
        )
        result = service.compact(conversation["id"], keep_recent=2)
        assert result is not None
        compacted = result.summary.casefold()
        for expected in (
            "pytest -q tests/test_checkout.py",
            "assertionerror: checkout mismatch",
            "exit code 17",
            "src/checkout/service.py",
            "must not change the public wire contract",
            "validation failed",
        ):
            assert expected.casefold() in compacted
    finally:
        db.close()


def test_stuck_detector_is_bounded_and_resets_on_new_evidence():
    read_a = _observational_fingerprint(
        "read_file",
        {"path": "src/a.py"},
        "same-a",
    )
    read_b = _observational_fingerprint(
        "read_file",
        {"path": "src/b.py"},
        "same-b",
    )
    changed_b = _observational_fingerprint(
        "read_file",
        {"path": "src/b.py"},
        "new evidence from b",
    )
    assert read_a and read_b and changed_b

    assert _stuck_sequence([read_a, read_a, read_a])
    assert _stuck_sequence([read_a, read_b, read_a, read_b])
    assert not _stuck_sequence([read_a, read_b, read_a, changed_b])
    assert (
        _observational_fingerprint(
            "write_file",
            {"path": "src/a.py", "content": "changed"},
            "ok",
        )
        is None
    )

    assert not _completion_recovery_exhausted(2)
    assert _completion_recovery_exhausted(3)


def test_same_and_alternating_failures_share_the_deterministic_stuck_rule():
    assert _stuck_sequence(["failure-a", "failure-a", "failure-a"])
    assert _stuck_sequence(["failure-a", "failure-b", "failure-a", "failure-b"])
    assert not _stuck_sequence(
        ["failure-a", "failure-b", "failure-a", "new-failure-evidence"]
    )
