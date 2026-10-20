"""Functional selection and AppArmor diagnostics for host-backed Bubblewrap."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from kitt.security.execution_sandbox import ExecutionSandbox, SandboxUnavailable


def _executable(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return path


def test_probes_next_installed_binary_when_first_is_outdated_or_blocked(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    old = _executable(tmp_path, "old-bwrap")
    denied = _executable(tmp_path, "apparmor-blocked-bwrap")
    working = _executable(tmp_path, "working-bwrap")
    sandbox = ExecutionSandbox(workspace)
    probed = []

    def run(argv, **_kwargs):
        candidate = Path(argv[0])
        probed.append((candidate, argv[1]))
        if argv[1] == "--version":
            version = "0.11.1" if candidate == old else "0.12.0"
            return SimpleNamespace(returncode=0, stdout=f"bubblewrap {version}\n")
        if candidate == denied:
            return SimpleNamespace(
                returncode=1, stderr="bwrap: setting up uid map: Permission denied"
            )
        return SimpleNamespace(returncode=0, stderr="")

    with patch.object(sandbox, "_bwrap_candidates", return_value=[old, denied, working]):
        with patch("kitt.security.execution_sandbox.subprocess.run", side_effect=run):
            assert sandbox.bubblewrap_executable() == str(working)
            assert sandbox.is_strong_available("workspace-write")
    assert (old, "--version") in probed
    assert (old, "--die-with-parent") not in probed
    assert (denied, "--die-with-parent") in probed
    assert (working, "--die-with-parent") in probed


def test_denied_namespace_reports_which_binary_app_armor_needs(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    candidate = _executable(tmp_path, "bwrap")
    sandbox = ExecutionSandbox(workspace)

    def run(argv, **_kwargs):
        if argv[1] == "--version":
            return SimpleNamespace(returncode=0, stdout="bubblewrap 0.12.0")
        return SimpleNamespace(
            returncode=1, stderr="bwrap: setting up uid map: Permission denied"
        )

    with patch.object(sandbox, "_bwrap_candidates", return_value=[candidate]):
        with patch("kitt.security.execution_sandbox.subprocess.run", side_effect=run):
            assert not sandbox.is_strong_available()
            with pytest.raises(SandboxUnavailable) as err:
                sandbox.plan(["/usr/bin/true"], workspace, require_strong=True)
    message = str(err.value)
    assert str(candidate) in message
    assert "Permission denied" in message
    assert "AppArmor" in message
    assert "bubblewrap >= 0.12" in message


def test_candidates_include_local_and_path_but_never_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace_bin = workspace / "bin"
    workspace_bin.mkdir()
    _executable(workspace_bin, "bwrap")
    bin_dir = tmp_path / "custom-bin"
    bin_dir.mkdir()
    chosen = _executable(bin_dir, "bwrap")
    sandbox = ExecutionSandbox(workspace)
    with patch("kitt.security.execution_sandbox.os.get_exec_path",
               return_value=[str(workspace_bin), str(bin_dir), str(bin_dir)]):
        candidates = sandbox._bwrap_candidates()
    assert chosen.resolve() in candidates
    assert workspace_bin.joinpath("bwrap").resolve() not in candidates
    assert candidates.count(chosen.resolve()) == 1


def test_failed_probe_is_refreshed_after_bounded_cache_interval(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    candidate = _executable(tmp_path, "bwrap")
    sandbox = ExecutionSandbox(workspace)
    current_time = [100.0]
    healthy = [False]

    def run(argv, **_kwargs):
        if argv[1] == "--version":
            return SimpleNamespace(returncode=0, stdout="bubblewrap 0.12.0")
        return SimpleNamespace(
            returncode=0 if healthy[0] else 1,
            stderr="" if healthy[0] else "setting up uid map: Permission denied",
        )

    with patch.object(sandbox, "_bwrap_candidates", return_value=[candidate]), (
        patch("kitt.security.execution_sandbox.time.monotonic",
              side_effect=lambda: current_time[0])
    ), patch("kitt.security.execution_sandbox.subprocess.run", side_effect=run):
        assert not sandbox.is_strong_available()
        healthy[0] = True
        assert not sandbox.is_strong_available()  # Cached negative outcome.
        current_time[0] += 61
        assert sandbox.is_strong_available()
        assert sandbox.bubblewrap_executable() == str(candidate)
