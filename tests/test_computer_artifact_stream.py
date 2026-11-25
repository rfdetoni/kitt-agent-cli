from io import BytesIO

import pytest

from kitt.artifacts.store import ArtifactStore
from kitt.history.database import HistoryDatabase
from kitt.history.repository import HistoryRepository


def test_streamed_artifact_transfer_checks_scope_and_hash(tmp_path):
    db = HistoryDatabase(tmp_path)
    history = HistoryRepository(db)
    workspace = history.get_or_create_workspace(str(tmp_path))
    conversation = history.create_conversation(workspace["id"], "transfer")
    other = history.create_conversation(workspace["id"], "other")
    source = ArtifactStore(str(tmp_path / "source"), db, inline_limit=16)
    target = ArtifactStore(str(tmp_path / "target"), db, inline_limit=16)
    data = b"abcdefgh" * 10000
    try:
        stream = BytesIO(data)
        artifact = source.put_stream(workspace["id"], iter(lambda: stream.read(1024), b""),
                                     "text", "stream", conversation_id=conversation["id"],
                                     sensitivity="SENSITIVE")
        assert artifact.storage_kind == "FILE"
        with pytest.raises(KeyError):
            source.transfer_to(artifact.id, target, workspace_id=workspace["id"],
                               conversation_id=other["id"])
        copied = source.transfer_to(artifact.id, target, workspace_id=workspace["id"],
                                    conversation_id=conversation["id"])
        assert target.read(copied.id) == data
        assert copied.content_hash == artifact.content_hash
        assert copied.sensitivity == "SENSITIVE"
        with pytest.raises(ValueError, match="integrity"):
            target.put_stream(workspace["id"], [b"x" * 100], "text", "bad",
                              expected_hash="0" * 64)
        assert not list(target.storage.glob(".stage-*"))
    finally:
        db.close()
