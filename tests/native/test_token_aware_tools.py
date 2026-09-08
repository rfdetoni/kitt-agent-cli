from __future__ import annotations

from types import SimpleNamespace

from kitt.native.output import OutputOptimizer
from kitt.tools.handlers.files import ListFilesHandler, ReadFileHandler
from kitt.tools.handlers.system import GitDiffHandler, GitStatusHandler


class NativeStub:
    def __init__(self):
        self.status = SimpleNamespace(backend="rust", available=True)
        self.read_calls = []
        self.list_calls = []

    def read_file(self, path, start_line=1, end_line=None, max_bytes=0, token_budget=1200):
        self.read_calls.append((path, start_line, end_line, max_bytes, token_budget))
        return {
            "path": path,
            "content": "a\nb",
            "content_hash": "range",
            "full_file_hash": "full",
            "start_line": 1,
            "end_line": 2,
            "total_lines": 100,
            "omitted_lines": 98,
            "next_start_line": 3,
            "estimated_tokens": 1,
            "file_size": 200,
            "mtime_ns": 10,
        }

    def list_files(self, path=".", limit=100, token_budget=600):
        self.list_calls.append((path, limit, token_budget))
        return {"files": ["a.py", "b.py"], "omitted": 7, "estimated_tokens": 2}


class CompressStub:
    def compress_output(self, argv, stdout, stderr, returncode, token_budget=1200):
        raw = stdout + (("\n" + stderr) if stderr else "")
        return {
            "output": "FAIL compact",
            "family": "build_test",
            "changed": True,
            "raw_bytes": len(raw.encode()),
            "output_bytes": len(b"FAIL compact"),
            "omitted_lines": 100,
            "raw_sha256": "x",
        }


def test_output_optimizer_propagates_budget_and_counts_tokens():
    optimizer = OutputOptimizer(CompressStub())
    result = optimizer.optimize(["pytest"], "x" * 5000, "", 1, token_budget=96)
    assert result.changed
    assert result.output == "FAIL compact"
    assert result.tokens_saved > 0
    assert result.output_estimated_tokens < result.raw_estimated_tokens


def test_native_file_read_is_bounded(tmp_path):
    target = tmp_path / "a.py"
    target.write_text("a\nb\n" + "c\n" * 100)
    native = NativeStub()
    registry = SimpleNamespace(root_path=tmp_path, native_engine=native)
    from kitt.security.workspace_fs import WorkspaceFileSystem
    registry.path_policy = SimpleNamespace()
    ctx = SimpleNamespace(
        registry=registry,
        security_context=None,
        workspace_id="w",
        conversation_id="c",
        turn_id="t",
    )
    result = ReadFileHandler().execute({"path": "a.py", "max_tokens": 128}, ctx)
    assert result.success
    assert result.truncated
    assert result.metadata["method"] == "native"
    assert result.metadata["next_start_line"] == 3
    assert native.read_calls[0][-1] == 128


def test_native_list_reports_omitted(tmp_path):
    (tmp_path / "a.py").write_text("")
    native = NativeStub()
    registry = SimpleNamespace(root_path=tmp_path, native_engine=native)
    ctx = SimpleNamespace(
        registry=registry,
        security_context=None,
        workspace_id="w",
        conversation_id="c",
        turn_id="t",
    )
    result = ListFilesHandler().execute({"path": ".", "limit": 2, "max_tokens": 128}, ctx)
    assert result.success
    assert "7 file(s) omitted" in result.output
    assert result.metadata["method"] == "native"
