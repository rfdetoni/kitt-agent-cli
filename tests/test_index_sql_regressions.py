"""Regressions for incremental index updates and FTS ownership."""
import sqlite3

from kitt.index.repository import RepositoryIndex
from kitt.index.schema import INDEX_SCHEMA_SQL


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
