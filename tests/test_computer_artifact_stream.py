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


def test_zstd_frames_are_bounded_verified_and_skip_incompressible_payloads():
    import os
    from kitt.artifacts.transfer_codec import decode_frame, encode_frame
    raw = b"a" * 65536
    packed = encode_frame(raw)
    assert len(packed) < 100
    assert decode_frame(packed) == raw
    random = os.urandom(65536)
    assert decode_frame(encode_frame(random)) == random
    assert decode_frame(encode_frame(raw, compress=False)) == raw
    for frame in (packed[:-1], packed + b"x", b"\xff" + packed[1:], b"\x01\x00\x02\x00\x00" + packed[5:]):
        with pytest.raises(ValueError):
            decode_frame(frame)


def test_compressed_stream_import_rejects_modified_artifact(tmp_path):
    db = HistoryDatabase(tmp_path)
    history = HistoryRepository(db)
    workspace = history.get_or_create_workspace(str(tmp_path))
    conversation = history.create_conversation(workspace["id"], "compressed")
    store = ArtifactStore(str(tmp_path), db, inline_limit=16)
    try:
        original = store.put(workspace["id"], b"xyz" * 50000, "text", "original",
                             conversation_id=conversation["id"])
        frames = list(store.export_frames(original.id, workspace_id=workspace["id"],
                                          conversation_id=conversation["id"]))
        copy = store.import_frames(workspace["id"], conversation["id"], frames,
                                   expected_hash=original.content_hash, artifact_type="text",
                                   summary="copy")
        assert copy.content_hash == original.content_hash
        assert store.read(copy.id) == store.read(original.id)
        with pytest.raises(ValueError, match="integrity"):
            store.import_frames(workspace["id"], conversation["id"], frames[:-1],
                                expected_hash=original.content_hash, artifact_type="text",
                                summary="truncated")
    finally:
        db.close()
