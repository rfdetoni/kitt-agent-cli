"""Regressions for incremental index updates and FTS ownership."""
import sqlite3

from kitt.index.repository import RepositoryIndex
from kitt.index.schema import INDEX_SCHEMA_SQL


def test_index_startup_uses_memory_when_disk_writer_is_busy(tmp_path, monkeypatch):
    """Another KITT daemon's write transaction cannot crash a new CLI."""
    import kitt.index.repository as repository

    monkeypatch.setattr(repository, "INDEX_OPEN_BUSY_TIMEOUT_SECONDS", 0.05)
    (tmp_path / "one.py").write_text(
        "def one():\n    return 'persistent_only_token'\n", encoding="utf-8",
    )
    with RepositoryIndex(tmp_path) as first:
        first.update_paths(["one.py"])
        assert first.metadata()["index_storage"] == "persistent"
        db_path = first.db_path

    holder = sqlite3.connect(str(db_path), isolation_level=None)
    try:
        holder.execute("BEGIN IMMEDIATE")
        holder.execute(
            "UPDATE index_meta SET value=value WHERE key='schema_version'"
        )
        (tmp_path / "two.py").write_text(
            "def two():\n    return 'ephemeral_only_token'\n", encoding="utf-8",
        )
        with RepositoryIndex(tmp_path) as second:
            assert second.in_memory
            assert second.db_path == ":memory:"
            meta = second.metadata()
            assert meta["index_storage"] == "memory_fallback"
            assert "locked" in meta["index_fallback_reason"]
            second.update_paths(["one.py", "two.py"])
            assert second.search_text("ephemeral_only_token")
            assert second.search_text("persistent_only_token")
        # The disk writer is still active and its transaction was untouched.
        assert holder.in_transaction
    finally:
        holder.rollback()
        holder.close()

    with RepositoryIndex(tmp_path) as recovered:
        assert recovered.metadata()["index_storage"] == "persistent"
        assert recovered.search_text("persistent_only_token")
        assert not recovered.search_text("ephemeral_only_token")


def test_index_fallback_is_only_for_database_lock_errors():
    assert RepositoryIndex._is_database_busy(
        sqlite3.OperationalError("database is locked")
    )
    assert RepositoryIndex._is_database_busy(
        sqlite3.OperationalError("database table is locked")
    )
    assert not RepositoryIndex._is_database_busy(
        sqlite3.OperationalError("no such table: index_meta")
    )


def test_qualified_symbol_and_chunk_indexes():
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(INDEX_SCHEMA_SQL)
        symbols = {r[1] for r in conn.execute("PRAGMA index_list('symbols')")}
        chunks = {r[1] for r in conn.execute("PRAGMA index_list('chunks')")}
        assert "idx_symbols_qualified_name" in symbols
        assert "idx_symbols_file_start" in symbols
        assert "idx_chunks_file" in chunks
    finally:
        conn.close()


def test_fts_update_and_file_deletion_keep_other_file(tmp_path):
    first = tmp_path / "alpha.py"
    second = tmp_path / "beta.py"
    first.write_text("def alpha():\n    return 'old_unique_token'\n", encoding="utf-8")
    second.write_text("def beta():\n    return 'other_unique_token'\n", encoding="utf-8")
    with RepositoryIndex(tmp_path) as index:
        index.update_paths(["alpha.py", "beta.py"])
        assert index.search_text("old_unique_token")
        assert index.search_text("other_unique_token")
        first.write_text("def alpha():\n    return 'new_unique_token_replacement'\n", encoding="utf-8")
        index.update_paths(["alpha.py"])
        assert not index.search_text("old_unique_token")
        assert index.search_text("new_unique_token_replacement")
        assert index.search_text("other_unique_token")
        first.unlink()
        index.update_paths(["alpha.py"])
        assert not index.search_text("new_unique_token_replacement")
        assert index.search_text("other_unique_token")


def test_full_reindex_skips_reads_on_unchanged_files(tmp_path, monkeypatch):
    source = tmp_path / "example.py"
    source.write_text("def example():\n    return 123\n", encoding="utf-8")
    with RepositoryIndex(tmp_path) as index:
        assert index.build_or_update()["updated"] >= 1
        def forbidden_read(*args, **kwargs):
            raise AssertionError("unchanged files must not be read again")
        monkeypatch.setattr(index.workspace_fs, "read", forbidden_read)
        assert index.build_or_update()["updated"] == 0

def test_background_parsing_does_not_block_interactive_sqlite_reads(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    (tmp_path / "blocked.py").write_text("def blocked():\n    return 1\n", encoding="utf-8")
    with RepositoryIndex(tmp_path) as index:
        parsing = Event()
        release = Event()
        original = index.parser_registry.parse

        def slow_parse(*args, **kwargs):
            parsing.set()
            release.wait(5)
            return original(*args, **kwargs)

        monkeypatch.setattr(index.parser_registry, "parse", slow_parse)
        with ThreadPoolExecutor(max_workers=2) as executor:
            building = executor.submit(index.build_or_update)
            try:
                assert parsing.wait(4), "index parser was not reached"
                query = executor.submit(index.metadata)
                assert "state" in query.result(timeout=1), "metadata blocked by parser"
            finally:
                release.set()
                building.result(timeout=6)

def test_incremental_graph_source_replacement_preserves_other_reverse_edges():
    from kitt.index.graph import RepositoryGraph

    graph = RepositoryGraph()
    graph.add_edge("a.py", "shared.py")
    graph.add_edge("b.py", "shared.py")
    graph.add_edge("a.py", "old.py")
    graph.remove_outgoing("a.py")
    assert ("b.py", 1.0) in graph.rev_adj["shared.py"]
    assert "a.py" not in [source for source, _ in graph.rev_adj["shared.py"]]
    graph.add_edge("a.py", "new.py")
    graph.add_edge("b.py", "shared.py", 2.0)
    assert ("b.py", 2.0) in graph.rev_adj["shared.py"]
    assert graph.adj["a.py"] == [("new.py", 1.0)]
