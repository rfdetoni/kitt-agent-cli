"""Active project RE sharing is zero-copy, bounded and operator-owned."""
from __future__ import annotations

import hashlib
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from kitt import re_workspace
from kitt.cli.main import build_parser
from kitt.vm_supervisor import VMError


def _fake_ghidra(monkeypatch, *, lines=None, interrupt=False, oom=False, polls=0):
    """No Docker engine, Ghidra or installed KITT CLI required for these contracts."""
    if lines is None:
        lines = (b"INFO REPORT: Analysis succeeded for file: /2.1.308\n"
                 b"INFO REPORT: Save succeeded for: /2.1.308\n")
    calls = []
    removed = []

    class FakeProc:
        returncode = None

        def __init__(self):
            self.polls = polls

        def wait(self, timeout=None):
            if interrupt:
                raise KeyboardInterrupt()
            if self.polls:
                self.polls -= 1
                raise subprocess.TimeoutExpired(cmd="docker", timeout=timeout)
            self.returncode = 0
            return 0

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def kill(self):
            self.returncode = -9

    def fake_popen(argv, **kwargs):
        calls.append((argv, kwargs))
        kwargs["stdout"].write(lines)
        kwargs["stdout"].flush()
        return FakeProc()

    def docker(argv, **kwargs):
        if argv[:2] == ["rm", "--force"]:
            removed.append(argv[2])
        if argv[:2] == ["container", "inspect"]:
            return SimpleNamespace(returncode=0,
                                   stdout=b'{"OOMKilled": true}' if oom
                                   else b'{"OOMKilled": false}')
        if argv[:2] == ["stats", "--no-stream"]:
            return SimpleNamespace(returncode=0,
                stdout=b'{"CPUPerc":"13.8%","MemUsage":"900MiB / 4GiB","BlockIO":"3MB / 5MB"}')
        return SimpleNamespace(returncode=0, stdout=b"")

    monkeypatch.setattr(re_workspace.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(re_workspace.re_docker_lab, "_docker", docker)
    return calls, removed


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
    monkeypatch.setattr(re_workspace.subprocess, "Popen",
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
    calls, removed = _fake_ghidra(monkeypatch)
    result = re_workspace.analyze_workspace(workspace=tmp_path, path="2.1.308")
    assert result["status"] == "GHIDRA_COMPLETED"
    assert result["workspace_shared_read_only"]
    assert result["source_copied"] is False
    argv = calls[0][0]
    assert "run" in argv and "--rm" not in argv and "--pull" in argv
    assert removed and removed[0].startswith("kitt-re-job-")
    assert "-analysisTimeoutPerFile" not in argv
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
    _fake_ghidra(monkeypatch, interrupt=True)
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



def _legacy_saved_job(tmp_path, source="2.1.308", job="job-27ee75656097"):
    output = tmp_path / "reverse-engineering" / "decompiled" / "ghidra" / job
    output.mkdir(parents=True)
    (output / "KittAnalysis.gpr").write_bytes(b"")
    (output / "KittAnalysis.rep").mkdir()
    old_log = output / "docker-analysis.log"
    old_log.write_text(
        "INFO IMPORTING: file:///workspace/" + source + "\n"
        "INFO REPORT: Save succeeded for: /" + Path(source).name + "\n"
        "INFO REPORT: Import succeeded\n"
    )
    return output, old_log


def test_resume_saved_legacy_project_uses_process_not_import_and_preserves_logs(
    monkeypatch, tmp_path
):
    (tmp_path / "2.1.308").write_bytes(b"\x7fELF" + bytes(128))
    output, old_log = _legacy_saved_job(tmp_path)
    receipt = old_log.read_bytes()
    monkeypatch.setattr(re_workspace, "_managed_image", lambda: "sha256:" + "a" * 64)
    calls, _ = _fake_ghidra(monkeypatch)
    result = re_workspace.analyze_workspace(
        workspace=tmp_path, path="2.1.308", resume_job="job-27ee75656097"
    )
    assert result["status"] == "GHIDRA_COMPLETED"
    assert result["mode"] == "resume"
    assert result["output"] == str(output)
    assert result["resume_job"] == "job-27ee75656097"
    argv, kwargs = calls[0]
    assert "-process" in argv and argv[argv.index("-process") + 1] == "2.1.308"
    assert "-import" not in argv
    assert "-analysisTimeoutPerFile" not in argv
    assert "timeout" not in kwargs
    assert result["analysis_timeout_seconds"] is None
    assert result["host_timeout_seconds"] is None
    assert "resume-" in result["log"]
    assert old_log.read_bytes() == receipt
    assert Path(result["log"]).is_file()
    assert not (output / "kitt-re-input.json").exists()


def test_analysis_timeout_saved_by_ghidra_is_not_reported_as_success(
    monkeypatch, tmp_path
):
    (tmp_path / "2.1.308").write_bytes(b"\x7fELF" + bytes(128))
    monkeypatch.setattr(re_workspace, "_managed_image", lambda: "sha256:" + "a" * 64)

    _fake_ghidra(monkeypatch, lines=(
        b"INFO REPORT: Save succeeded for: /2.1.308\n"
        b"ERROR REPORT: Analysis timed out at 600 seconds. "
        b"Processing not completed for file: file:///workspace/2.1.308\n"
    ))
    result = re_workspace.analyze_workspace(workspace=tmp_path, path="2.1.308")
    assert result["analysis_timed_out"] is True
    assert result["status"] == "ANALYSIS_TIMED_OUT"
    assert result["mode"] == "import"
    assert Path(result["output"], "kitt-re-input.json").is_file()


def test_resume_rejects_mismatched_project_and_symlink(monkeypatch, tmp_path):
    (tmp_path / "2.1.308").write_bytes(b"\x7fELF" + bytes(128))
    output, _ = _legacy_saved_job(tmp_path, source="wrong.elf")
    monkeypatch.setattr(re_workspace, "_managed_image", lambda: "sha256:" + "a" * 64)
    with pytest.raises(VMError, match="receipt"):
        re_workspace.analyze_workspace(
            workspace=tmp_path, path="2.1.308",
            resume_job="job-27ee75656097", dry_run=True,
        )
    with pytest.raises(VMError, match="Invalid RE resume"):
        re_workspace.analyze_workspace(
            workspace=tmp_path, path="2.1.308", resume_job="../outside", dry_run=True,
        )
    (output / "docker-analysis.log").unlink()
    (output / "docker-analysis.log").symlink_to(tmp_path / "2.1.308")
    with pytest.raises(VMError, match="receipt"):
        re_workspace.analyze_workspace(
            workspace=tmp_path, path="2.1.308", resume_job=output.name, dry_run=True,
        )


def test_parser_optional_limits_are_turned_off_by_default():
    args = build_parser().parse_args([
        "re", "lab", "analyze", "--path", "2.1.308",
        "--host-timeout-seconds", "14400", "--analysis-timeout-seconds", "13000",
        "--memory-mib", "3072", "--heap-mib", "1536", "--cpus", "1"])
    assert args.host_timeout_seconds == 14400
    assert args.analysis_timeout_seconds == 13000
    assert (args.memory_mib, args.heap_mib, args.cpus) == (3072, 1536, 1)
    default = build_parser().parse_args(["re", "lab", "analyze", "--path", "2.1.308"])
    assert default.host_timeout_seconds is None
    assert default.analysis_timeout_seconds is None


def test_optional_ghidra_timeout_and_memory_are_applied_only_on_request(monkeypatch, tmp_path):
    (tmp_path / "2.1.308").write_bytes(b"\x7fELF" + bytes(128))
    monkeypatch.setattr(re_workspace, "_managed_image", lambda: "sha256:" + "a" * 64)
    calls, _ = _fake_ghidra(monkeypatch)
    result = re_workspace.analyze_workspace(
        workspace=tmp_path, path="2.1.308", analysis_timeout_seconds=4000,
        host_timeout_seconds=6000, memory_mib=3072, heap_mib=1536, cpus=1,
    )
    argv = calls[0][0]
    assert argv[argv.index("-analysisTimeoutPerFile") + 1] == "4000"
    assert "GHIDRA_HEADLESS_MAXMEM=1536M" in argv
    assert argv[argv.index("--memory") + 1] == "3072m"
    assert result["status"] == "GHIDRA_COMPLETED"
    assert result["completion_scope"] == "ghidra_report_only"


def test_slow_active_process_is_not_cancelled_by_old_limit(monkeypatch, tmp_path):
    (tmp_path / "2.1.308").write_bytes(b"\x7fELF" + bytes(128))
    monkeypatch.setattr(re_workspace, "_managed_image", lambda: "sha256:" + "a" * 64)
    calls, removed = _fake_ghidra(monkeypatch, polls=3)
    result = re_workspace.analyze_workspace(workspace=tmp_path, path="2.1.308")
    assert result["status"] == "GHIDRA_COMPLETED"
    assert result["telemetry_available"] is True
    assert len(calls) == 1 and len(removed) == 1
    assert result["host_timeout_seconds"] is None


def test_silence_and_unavailable_stats_warn_but_do_not_kill(monkeypatch, tmp_path, capsys):
    log = tmp_path / "quiet.log"
    log.write_bytes(b"")
    monkeypatch.setattr(re_workspace, "_sample_stats", lambda name: None)
    monitor = re_workspace._ProgressMonitor(
        log, "kitt-re-job-" + "f" * 12, time.monotonic() - 900, 600)
    monitor.report()
    assert monitor.state == "RUNNING_NO_RECENT_SIGNAL"
    assert "unavailable" in capsys.readouterr().err


def test_active_cpu_prevents_false_stalled_warning(monkeypatch, tmp_path):
    log = tmp_path / "quiet.log"
    log.write_bytes(b"")
    monkeypatch.setattr(re_workspace, "_sample_stats",
                        lambda name: {"CPUPerc": "15%", "MemUsage": "100MiB / 4GiB"})
    monitor = re_workspace._ProgressMonitor(
        log, "kitt-re-job-" + "f" * 12, time.monotonic() - 900, 600)
    monitor.report()
    assert monitor.state == "STARTING"


def test_oom_killed_requires_real_inspect_evidence(monkeypatch, tmp_path):
    (tmp_path / "2.1.308").write_bytes(b"\x7fELF" + bytes(128))
    monkeypatch.setattr(re_workspace, "_managed_image", lambda: "sha256:" + "a" * 64)
    _, removed = _fake_ghidra(monkeypatch, oom=True)
    result = re_workspace.analyze_workspace(workspace=tmp_path, path="2.1.308")
    assert result["status"] == "OOM"
    assert result["oom_killed"] and removed


def test_interrupt_returns_cancelled_with_saved_logs(monkeypatch, tmp_path):
    (tmp_path / "2.1.308").write_bytes(b"\x7fELF" + bytes(128))
    monkeypatch.setattr(re_workspace, "_managed_image", lambda: "sha256:" + "a" * 64)
    _, removed = _fake_ghidra(monkeypatch, interrupt=True)
    result = re_workspace.analyze_workspace(workspace=tmp_path, path="2.1.308")
    assert result["status"] == "CANCELLED"
    assert removed and Path(result["log"]).exists()


def test_exclusive_saved_project_lock(monkeypatch, tmp_path):
    (tmp_path / "2.1.308").write_bytes(b"\x7fELF" + bytes(128))
    output, _ = _legacy_saved_job(tmp_path)
    with re_workspace._project_lock(output):
        with pytest.raises(VMError, match="already being analyzed"):
            with re_workspace._project_lock(output):
                pass


def test_heap_cannot_consume_all_container_memory(monkeypatch, tmp_path):
    (tmp_path / "2.1.308").write_bytes(b"\x7fELF" + bytes(128))
    monkeypatch.setattr(re_workspace, "_managed_image", lambda: "sha256:" + "a" * 64)
    with pytest.raises(VMError, match="headroom"):
        re_workspace.analyze_workspace(
            workspace=tmp_path, path="2.1.308", memory_mib=1024, heap_mib=1000,
            dry_run=True)
