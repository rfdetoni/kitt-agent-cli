from __future__ import annotations

from kitt.artifacts.store import ArtifactStore
from kitt.core.workspace_snapshot import WorkspaceSnapshotService
from kitt.history.database import HistoryDatabase
from kitt.history.repository import HistoryRepository


def test_selective_snapshot_diff_preview_and_restore_leave_other_paths_untouched(tmp_path):
    (tmp_path / "a.txt").write_text("A0", encoding="utf-8")
    (tmp_path / "b.txt").write_text("B0", encoding="utf-8")

    db = HistoryDatabase(str(tmp_path))
    repo = HistoryRepository(db)
    workspace = repo.get_or_create_workspace(str(tmp_path))
    conversation = repo.create_conversation(workspace["id"], "snapshot")
    artifacts = ArtifactStore(str(tmp_path), db, ephemeral=True)
    service = WorkspaceSnapshotService(
        tmp_path,
        workspace_id=workspace["id"],
        artifact_store=artifacts,
    )

    snapshot = service.capture(
        conversation_id=conversation["id"],
        turn_id="turn-snapshot",
        paths=["a.txt", "b.txt"],
    )
    (tmp_path / "a.txt").write_text("A1", encoding="utf-8")
    (tmp_path / "b.txt").write_text("B1", encoding="utf-8")

    diff = service.diff(
        snapshot.snapshot_id,
        conversation_id=conversation["id"],
        turn_id="turn-snapshot",
        paths=["a.txt"],
    )
    assert [item["path"] for item in diff] == ["a.txt"]
    assert diff[0]["status"] == "MODIFIED"

    preview = service.preview_restore(
        snapshot.snapshot_id,
        conversation_id=conversation["id"],
        turn_id="turn-snapshot",
        paths=["a.txt"],
    )
    assert preview == [{**diff[0], "restore_action": "RESTORE"}]

    restored = service.restore(
        snapshot.snapshot_id,
        conversation_id=conversation["id"],
        turn_id="turn-snapshot",
        paths=["a.txt"],
    )
    assert restored == ["a.txt"]
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "A0"
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "B1"

    # Automatic rollback must preserve a newer edit by another actor.
    newer = service.capture(conversation_id=conversation["id"], turn_id="turn-snapshot", paths=["a.txt"])
    (tmp_path / "a.txt").write_text("agent edit", encoding="utf-8")
    guard = {"a.txt": service.fs.read("a.txt").sha256}
    (tmp_path / "a.txt").write_text("user edit", encoding="utf-8")
    import pytest
    with pytest.raises(ValueError, match="Rollback conflict"):
        service.restore(newer.snapshot_id, conversation_id=conversation["id"],
                        turn_id="turn-snapshot", expected_current=guard)
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "user edit"

    artifacts.close()
    db.close()


def test_selective_snapshot_rejects_paths_outside_snapshot(tmp_path):
    (tmp_path / "a.txt").write_text("A0", encoding="utf-8")
    db = HistoryDatabase(str(tmp_path))
    repo = HistoryRepository(db)
    workspace = repo.get_or_create_workspace(str(tmp_path))
    conversation = repo.create_conversation(workspace["id"], "snapshot")
    artifacts = ArtifactStore(str(tmp_path), db, ephemeral=True)
    service = WorkspaceSnapshotService(
        tmp_path,
        workspace_id=workspace["id"],
        artifact_store=artifacts,
    )
    snapshot = service.capture(
        conversation_id=conversation["id"],
        turn_id="turn-snapshot",
        paths=["a.txt"],
    )

    import pytest

    with pytest.raises(ValueError, match="not part of snapshot"):
        service.restore(
            snapshot.snapshot_id,
            conversation_id=conversation["id"],
            turn_id="turn-snapshot",
            paths=["b.txt"],
        )

    artifacts.close()
    db.close()
