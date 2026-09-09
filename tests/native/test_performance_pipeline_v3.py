from __future__ import annotations

import io
import sys
from types import SimpleNamespace

from kitt.runtime.safe_runtime import SafeRuntime
from kitt.security.context import ExecutionSecurityContext
from kitt.security.workspace_fs import WorkspaceFileSystem
from kitt.tools.handlers.files import ListFilesHandler
from kitt.tools.handlers.search import SearchHandler, indexed_literal_search
from kitt.tools.process_runner import ProcessRunner, _HeadTailCapture


def _ctx(tmp_path, *, security_context=None, index=None, native=None):
    registry = SimpleNamespace(
        root_path=tmp_path,
        repository_index=index,
        native_engine=native,
        _refresh_index=lambda *args, **kwargs: None,
    )
    return SimpleNamespace(
        registry=registry,
        security_context=security_context,
        workspace_id="w",
        conversation_id="c",
        turn_id="t",
    )


def test_head_tail_capture_retains_final_diagnostic():
    capture = _HeadTailCapture(4096)
    payload = b"BEGIN\n" + (b"x" * 20000) + b"\nFINAL_ASSERTION_ERROR\n"
    capture.consume(io.BytesIO(payload))
    rendered = capture.render()
    assert capture.truncated
    assert rendered.startswith(b"BEGIN")
    assert b"FINAL_ASSERTION_ERROR" in rendered
    assert b"KITT capture omitted" in rendered
    assert len(rendered) <= 4096


def test_process_runner_preserves_both_stream_tails(tmp_path):
    runner = ProcessRunner(str(tmp_path), max_output_bytes=8192)
    code = (
        "import sys;"
        "sys.stdout.write('OUT_HEAD\\n' + 'o'*50000 + '\\nOUT_TAIL\\n');"
        "sys.stderr.write('ERR_HEAD\\n' + 'e'*50000 + '\\nERR_TAIL\\n')"
    )
    result = runner.run([sys.executable, "-c", code], timeout_seconds=10)
    assert result.returncode == 0
    assert result.truncated
    assert "OUT_HEAD" in result.stdout
    assert "OUT_TAIL" in result.stdout
    assert "ERR_HEAD" in result.stderr
    assert "ERR_TAIL" in result.stderr
    assert len(result.stdout.encode()) + len(result.stderr.encode()) <= 8192


def test_workspace_listing_is_deterministic(tmp_path):
    for name in ["z.py", "A.py", "m.py", "b.py"]:
        (tmp_path / name).write_text(name)
    fs = WorkspaceFileSystem(tmp_path)
    first = fs.list_regular_files(".", limit=10)
    second = fs.list_regular_files(".", limit=10)
    assert first == second
    assert first == sorted(first, key=str.casefold)


def test_scoped_list_does_not_lose_allowed_file_after_large_unrelated_prefix(tmp_path):
    for i in range(600):
        (tmp_path / f"a{i:04}.txt").write_text("")
    (tmp_path / "z_allowed.txt").write_text("allowed")

    security = ExecutionSecurityContext.create_user_context(
        "w", "c", path_scope=["z_allowed.txt"]
    )
    result = ListFilesHandler().execute(
        {"path": ".", "limit": 10, "max_tokens": 128},
        _ctx(tmp_path, security_context=security),
    )
    assert result.success
    assert result.output.strip() == "z_allowed.txt"
    assert not result.truncated


class _ReadyIndex:
    def metadata(self):
        return {"state": "READY"}

    def search_text(self, query, limit=20):
        return [
            {
                "path": "src/demo.py",
                "content": "zero\nTarget Needle\nlast",
                "start_line": 100,
                "end_line": 102,
                "score": -1.0,
            }
        ]


class _NativeBomb:
    status = SimpleNamespace(backend="rust")

    def search(self, *args, **kwargs):
        raise AssertionError("literal READY-index search must not rescan repository")


def test_indexed_literal_search_reports_absolute_file_line():
    data = indexed_literal_search(
        _ReadyIndex(),
        "target needle",
        max_results=10,
        token_budget=128,
    )
    assert data is not None
    assert data["hits"][0]["line"] == 101
    assert data["backend"] == "index"


def test_search_handler_prefers_ready_index_for_literal_query(tmp_path):
    result = SearchHandler().execute(
        {"pattern": "target needle", "max_tokens": 128},
        _ctx(tmp_path, index=_ReadyIndex(), native=_NativeBomb()),
    )
    assert result.success
    assert "src/demo.py:101:Target Needle" in result.output
    assert result.metadata["method"] == "index"


class _SymbolEngine:
    status = SimpleNamespace(backend="rust")

    def read_symbol(self, value):
        return {
            "symbol": {
                "id": value,
                "path": "src/big.py",
                "name": "Big",
                "source_hash": "hash",
            },
            "source": "x" * 10000,
        }

    def find_symbols(self, value, limit=5):
        return []


def test_safe_runtime_read_symbol_obeys_max_tokens():
    runtime = SafeRuntime.__new__(SafeRuntime)
    runtime.registry = SimpleNamespace(native_engine=_SymbolEngine())
    result = runtime._op_repo_read_symbol(
        {"symbol_id": "symbol:big", "max_tokens": 64}, None
    )
    assert result.success
    assert result.data["truncated"] is True
    assert len(result.data["source"].encode("utf-8")) <= 64 * 4


class _Memory:
    def query(self, query, limit=5):
        return [
            {"id": "1", "source": "memory", "text": "x" * 10000, "score": 1.0},
            {"id": "2", "source": "memory", "text": "second", "score": 0.5},
        ]


def test_safe_runtime_memory_query_is_context_bounded():
    runtime = SafeRuntime.__new__(SafeRuntime)
    runtime.memory = _Memory()
    result = runtime._op_memory_query({"query": "x", "limit": 5, "max_tokens": 64})
    assert result.success
    assert result.metadata["truncated"]
    encoded = sum(len(str(row).encode("utf-8")) for row in result.data)
    assert encoded < 1024
