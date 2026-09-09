from __future__ import annotations

from types import SimpleNamespace

from kitt.native.output import OutputOptimizer
from kitt.tools.handlers.files import ListFilesHandler, ReadFileHandler


class NativeStub:
    def __init__(self):
        self.status = SimpleNamespace(backend="rust", available=True)
        self.read_calls = []
        self.list_calls = []

    def read_file(self, *args, **kwargs):
        self.read_calls.append((args, kwargs))
        raise AssertionError("filesystem handler must not bypass WorkspaceFileSystem")

    def list_files(self, *args, **kwargs):
        self.list_calls.append((args, kwargs))
        raise AssertionError("filesystem handler must not bypass WorkspaceFileSystem")


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


class ArtifactStoreStub:
    def put(self, *args, **kwargs):
        return SimpleNamespace(id="artifact-1")


def _ctx(tmp_path, native=None, security_context=None):
    registry = SimpleNamespace(root_path=tmp_path, native_engine=native)
    return SimpleNamespace(
        registry=registry,
        security_context=security_context,
        workspace_id="w",
        conversation_id="c",
        turn_id="t",
    )


def test_output_optimizer_reports_unavailable_raw_capture_truthfully():
    optimizer = OutputOptimizer(CompressStub())
    result = optimizer.optimize(["pytest"], "x" * 5000, "", 1, token_budget=96)
    assert result.changed
    assert "FAIL compact" in result.output
    assert "raw capture unavailable" in result.output
    assert result.tokens_saved > 0
    assert not result.raw_capture_available
    assert not result.full_raw_recoverable


def test_output_optimizer_marks_complete_artifact_recoverable():
    optimizer = OutputOptimizer(CompressStub())
    result = optimizer.optimize(
        ["pytest"],
        "x" * 5000,
        "",
        1,
        artifact_store=ArtifactStoreStub(),
        token_budget=96,
    )
    assert result.changed
    assert result.raw_artifact_id == "artifact-1"
    assert result.raw_capture_available
    assert result.full_raw_recoverable
    assert "artifact-1" in result.output


def test_output_optimizer_marks_process_truncation_not_fully_recoverable():
    optimizer = OutputOptimizer(CompressStub())
    result = optimizer.optimize(
        ["pytest"],
        "x" * 5000,
        "",
        1,
        artifact_store=ArtifactStoreStub(),
        capture_truncated=True,
        raw_total_bytes=50_000,
        token_budget=96,
    )
    assert result.raw_capture_available
    assert not result.full_raw_recoverable
    assert "full=false" in result.output


def test_read_file_uses_workspace_fs_not_native(tmp_path):
    (tmp_path / "a.py").write_text("a\nb\n" + "c\n" * 100)
    native = NativeStub()
    result = ReadFileHandler().execute(
        {"path": "a.py", "max_tokens": 128}, _ctx(tmp_path, native=native)
    )
    assert result.success
    assert result.metadata["method"] == "workspace_fs"
    assert native.read_calls == []


def test_read_file_rejects_inverted_range(tmp_path):
    (tmp_path / "a.py").write_text("a\nb\n")
    result = ReadFileHandler().execute(
        {"path": "a.py", "start_line": 10, "end_line": 2}, _ctx(tmp_path)
    )
    assert not result.success
    assert "end_line" in (result.error or "")


def test_read_file_long_line_reports_partial_without_fake_resume(tmp_path):
    (tmp_path / "a.py").write_text("x" * 10_000 + "\nsecond\n")
    result = ReadFileHandler().execute(
        {"path": "a.py", "max_tokens": 64}, _ctx(tmp_path)
    )
    assert result.success
    assert result.truncated
    assert result.metadata["partial_line_truncated"] is True
    assert result.metadata["next_start_line"] is None
    assert len(result.output.encode("utf-8")) <= 64 * 4


def test_list_files_uses_workspace_fs_not_native(tmp_path):
    (tmp_path / "a.py").write_text("")
    (tmp_path / "b.py").write_text("")
    native = NativeStub()
    result = ListFilesHandler().execute(
        {"path": ".", "limit": 1, "max_tokens": 128},
        _ctx(tmp_path, native=native),
    )
    assert result.success
    assert result.metadata["method"] == "workspace_fs"
    assert native.list_calls == []
    assert result.truncated
