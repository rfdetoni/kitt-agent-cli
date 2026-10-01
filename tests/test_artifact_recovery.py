from __future__ import annotations

from kitt.artifacts.store import ArtifactStore
from kitt.history.database import HistoryDatabase
from kitt.history.repository import HistoryRepository


def _store(tmp_path):
    db = HistoryDatabase(str(tmp_path))
    repo = HistoryRepository(db)
    workspace = repo.get_or_create_workspace(str(tmp_path))
    store = ArtifactStore(
        str(tmp_path),
        db,
        inline_limit=64,
        page_bytes=1024,
    )
    return db, workspace["id"], store


def test_large_artifact_exact_recovery_by_bounded_pages(tmp_path):
    db, workspace_id, store = _store(tmp_path)
    try:
        content = "".join(
            f"line-{index:05d}: deterministic payload\n"
            for index in range(800)
        )
        artifact = store.put(
            workspace_id,
            content,
            "TOOL_OUTPUT",
            "large exact recovery",
        )
        assert artifact.storage_kind == "FILE"

        offset = 0
        parts = []
        while True:
            page = store.read_text_page(
                artifact.id,
                offset=offset,
                max_bytes=1024,
            )
            parts.append(page["content"])
            offset += page["bytes_returned"]
            if not page["has_more"]:
                break

        assert "".join(parts) == content
        assert offset == len(content.encode("utf-8"))
    finally:
        store.close()
        db.close()


def test_large_artifact_query_search_does_not_full_hydrate_file(tmp_path, monkeypatch):
    db, workspace_id, store = _store(tmp_path)
    try:
        content = (
            ("prefix payload\n" * 500)
            + "UNIQUE-NEEDLE surrounding evidence for recovery\n"
            + ("suffix payload\n" * 500)
        )
        artifact = store.put(
            workspace_id,
            content,
            "TOOL_OUTPUT",
            "large query recovery",
        )
        assert artifact.storage_kind == "FILE"

        def forbid_full_hydration(_artifact_id):
            raise AssertionError("FILE artifact search must not call read_text")

        monkeypatch.setattr(store, "read_text", forbid_full_hydration)
        hits = store.search_text(
            artifact.id,
            "unique-needle",
            limit=5,
            context_chars=80,
        )

        assert len(hits) == 1
        assert "UNIQUE-NEEDLE" in hits[0]["excerpt"]
        assert hits[0]["content_hash"] == artifact.content_hash
        assert hits[0]["offset"] == content.index("UNIQUE-NEEDLE")
    finally:
        store.close()
        db.close()


def test_artifact_query_search_detects_blob_tampering(tmp_path):
    db, workspace_id, store = _store(tmp_path)
    try:
        artifact = store.put(
            workspace_id,
            "payload " * 200,
            "TOOL_OUTPUT",
            "integrity",
        )
        target = store.storage / artifact.relative_storage_path
        target.write_bytes(b"tampered")

        try:
            store.search_text(artifact.id, "payload")
        except ValueError as exc:
            assert "integrity" in str(exc).lower()
        else:
            raise AssertionError("tampered artifact search must fail closed")
    finally:
        store.close()
        db.close()
