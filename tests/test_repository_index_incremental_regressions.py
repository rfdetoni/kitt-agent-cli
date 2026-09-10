from __future__ import annotations

from pathlib import Path

from kitt.index.repository import RepositoryIndex


def test_partial_scan_preserves_indexed_files_outside_scan_window(tmp_path: Path):
    (tmp_path / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def beta():\n    return 2\n", encoding="utf-8")

    index = RepositoryIndex(tmp_path, in_memory=True, max_files=10)
    try:
        first = index.build_or_update()
        assert first["state"] == "READY"
        assert index._conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 2

        index.max_files = 1
        second = index.build_or_update()

        assert second["state"] == "PARTIAL"
        assert second["deleted"] == 0
        paths = {
            row["path"]
            for row in index._conn.execute("SELECT path FROM files ORDER BY path").fetchall()
        }
        assert paths == {"a.py", "b.py"}
    finally:
        index.close()


def test_noop_reindex_skips_reference_and_fts_maintenance(tmp_path: Path, monkeypatch):
    (tmp_path / "app.py").write_text("def stable_symbol():\n    return 1\n", encoding="utf-8")

    index = RepositoryIndex(tmp_path, in_memory=True)
    try:
        first = index.build_or_update()
        assert first["generation"] == 1

        def fail_if_called():
            raise AssertionError("expensive index maintenance must not run for a no-op scan")

        monkeypatch.setattr(index, "_rebuild_reference_edges_locked", fail_if_called)
        monkeypatch.setattr(index, "_ensure_fts_consistency_locked", fail_if_called)

        second = index.build_or_update()

        assert second["updated"] == 0
        assert second["deleted"] == 0
        assert second["generation"] == first["generation"]
    finally:
        index.close()


def test_complete_scan_still_removes_deleted_files(tmp_path: Path):
    path = tmp_path / "obsolete.py"
    path.write_text("def obsolete():\n    return True\n", encoding="utf-8")

    index = RepositoryIndex(tmp_path, in_memory=True, max_files=10)
    try:
        index.build_or_update()
        assert index.search_symbol("obsolete")

        path.unlink()
        result = index.build_or_update()

        assert result["state"] == "READY"
        assert result["deleted"] == 1
        assert not index.search_symbol("obsolete")
    finally:
        index.close()
