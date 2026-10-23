"""Active project RE sharing is zero-copy, bounded and operator-owned."""
from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from kitt import re_workspace
from kitt.cli.main import build_parser
from kitt.vm_supervisor import VMError


def test_re_analyze_parser_accepts_active_project_and_read_only_plan():
    args = build_parser().parse_args(["re", "lab", "analyze", "--path", "2.1.308"])
    assert args.lab_action == "analyze"
    assert args.path == "2.1.308" and args.workspace is None
    args = build_parser().parse_args([
        "re", "lab", "analyze", "--workspace", "/tmp/project",
        "--path", "2.1.308", "--dry-run",
    ])
    assert args.dry_run and args.workspace == Path("/tmp/project")


def test_dry_run_uses_current_project_and_creates_nothing(monkeypatch, tmp_path):
    binary = tmp_path / "2.1.308"
    binary.write_bytes(b"\x7fELF\x02\x01" + bytes(122))
    sha = hashlib.sha256(binary.read_bytes()).hexdigest()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(re_workspace, "_managed_image", lambda: "sha256:" + "a" * 64)
    monkeypatch.setattr(re_workspace.subprocess, "run",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            AssertionError("dry-run must not execute Docker")))
    result = re_workspace.analyze_workspace(path="2.1.308", dry_run=True)
    assert result["status"] == "PLAN_ONLY"
    assert result["source_sha256"] == sha
    assert result["source_size"] == binary.stat().st_size
    assert result["workspace_mount"] == "read_only"
    assert result["no_copy"]
    assert not (tmp_path / "reverse-engineering").exists()


def test_real_plan_mounts_active_workspace_readonly_and_only_output_writable(monkeypatch, tmp_path):
    binary = tmp_path / "2.1.308"
    binary.write_bytes(b"\x7fELF\x02\x01" + bytes(122))
    before = binary.stat()
    monkeypatch.setattr(re_workspace, "_managed_image", lambda: "sha256:" + "a" * 64)
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(re_workspace.subprocess, "run", fake_run)
    result = re_workspace.analyze_workspace(workspace=tmp_path, path="2.1.308")
    assert result["status"] == "GHIDRA_COMPLETED"
    assert result["workspace_shared_read_only"]
    assert result["source_copied"] is False
    argv = calls[0][0]
    assert "run" in argv and "--rm" in argv and "--pull" in argv
    assert "none" in argv and "--cap-drop" in argv and "ALL" in argv
    assert "--read-only" in argv and "--memory" in argv
    assert f"type=bind,src={tmp_path},dst=/workspace,readonly" in argv
    assert f"type=bind,src={result['output']},dst=/output" in argv
    assert "/workspace/2.1.308" in argv
    assert "/opt/ghidra/support/analyzeHeadless" in argv
    assert "--privileged" not in argv and "/var/run/docker.sock" not in " ".join(argv)
    assert Path(result["output"]).is_dir()
    assert Path(result["log"]).is_file()
    after = binary.stat()
    assert (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
    assert binary.read_bytes() == b"\x7fELF\x02\x01" + bytes(122)


def test_readonly_workspace_rejects_symlinks_protected_paths_and_size(monkeypatch, tmp_path):
    monkeypatch.setattr(re_workspace, "_managed_image", lambda: "sha256:" + "b" * 64)
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main")
    with pytest.raises(VMError):
        re_workspace.analyze_workspace(workspace=tmp_path, path=".git/HEAD", dry_run=True)
    with pytest.raises(VMError):
        re_workspace.analyze_workspace(workspace=tmp_path, path="../outside", dry_run=True)
    outside = tmp_path / "file.elf"
    outside.write_bytes(b"\x7fELF" + bytes(60))
    try:
        (tmp_path / "link.elf").symlink_to(outside)
    except (OSError, NotImplementedError):
        pass
    else:
        with pytest.raises(VMError):
            re_workspace.analyze_workspace(workspace=tmp_path, path="link.elf", dry_run=True)
    huge = tmp_path / "huge.elf"
    with huge.open("wb") as handle:
        handle.truncate(512 * 1024 * 1024 + 1)
    with pytest.raises(VMError):
        re_workspace.analyze_workspace(workspace=tmp_path, path="huge.elf", dry_run=True)


def test_existing_output_symlink_is_not_adopted(monkeypatch, tmp_path):
    source = tmp_path / "test.elf"
    source.write_bytes(b"\x7fELF" + bytes(80))
    (tmp_path / "reverse-engineering").symlink_to(tmp_path)
    monkeypatch.setattr(re_workspace, "_managed_image", lambda: "sha256:" + "a" * 64)
    with pytest.raises(VMError, match="output directory"):
        re_workspace.analyze_workspace(workspace=tmp_path, path="test.elf")


def test_only_existing_managed_running_image_may_be_reused(monkeypatch):
    monkeypatch.setattr(re_workspace.re_docker_lab, "inspect", lambda role: None)
    with pytest.raises(VMError, match="not running"):
        re_workspace._managed_image()
    monkeypatch.setattr(re_workspace.re_docker_lab, "inspect",
                        lambda role: {"managed": True, "status": "running",
                                      "container_id": "a" * 64})
    monkeypatch.setattr(re_workspace.re_docker_lab, "_docker",
                        lambda *args, **kwargs: SimpleNamespace(
                            returncode=0, stdout=b"sha256:" + b"f" * 64))
    assert re_workspace._managed_image() == "sha256:" + "f" * 64



def test_progress_heartbeat_reports_live_log_without_claiming_ghidra_done(
    monkeypatch, tmp_path, capsys
):
    import time

    path = tmp_path / "docker-analysis.log"
    path.write_bytes(b"INFO Ghidra running\n")

    class StopAfterOne:
        count = 0

        def wait(self, seconds):
            self.count += 1
            assert seconds == re_workspace._PROGRESS_INTERVAL_SECONDS
            return self.count > 1

    re_workspace._report_progress(
        StopAfterOne(), path, time.monotonic() - 20,
    )
    captured = capsys.readouterr()
    assert captured.out == "", "JSON stdout must remain machine-readable"
    assert "Ghidra em execução" in captured.err
    assert str(path) in captured.err
    assert "bytes" in captured.err


def test_interrupt_cleans_up_only_its_own_analysis_container(
    monkeypatch, tmp_path, capsys
):
    (tmp_path / "2.1.308").write_bytes(b"\x7fELF\x02\x01" + bytes(122))
    monkeypatch.setattr(re_workspace, "_managed_image", lambda: "sha256:" + "a" * 64)
    monkeypatch.setattr(
        re_workspace.subprocess, "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    removed = []
    monkeypatch.setattr(
        re_workspace.re_docker_lab, "_docker",
        lambda argv, **kw: removed.append(argv) or SimpleNamespace(returncode=0),
    )
    with pytest.raises(VMError, match="cancelada"):
        re_workspace.analyze_workspace(workspace=tmp_path, path="2.1.308")
    assert len(removed) == 1
    assert removed[0][:2] == ["rm", "--force"]
    assert removed[0][2].startswith("kitt-re-job-")
    assert "kitt-re-full" not in " ".join(removed[0])
    assert "Iniciando Ghidra" in capsys.readouterr().err
