"""Broker operation and path constraints for untrusted model commands."""
from pathlib import Path
from unittest.mock import patch

import pytest

from kitt.security.autopilot_broker import _check_command, _relative_cwd
from kitt.security.execution_sandbox import ExecutionSandbox


def test_broker_rejects_workspace_escape(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "sub").mkdir()
    assert _relative_cwd("sub", root) == "sub"
    for value in ("../x", "/tmp", "sub/../../etc"):
        with pytest.raises(ValueError):
            _relative_cwd(value, root)


def test_broker_rejects_unbounded_commands():
    for argv in ([], ["sh", "x" * 70000], ["sh", "\x00"], [""]):
        with pytest.raises(ValueError):
            _check_command(argv)


def test_broker_plan_does_not_expose_docker_socket(tmp_path: Path):
    sandbox = ExecutionSandbox(tmp_path, auto_detect=False)
    with patch.object(sandbox, "_probe_broker", return_value="/run/kitt-broker/exec.sock"):
        plan = sandbox.plan(
            ["sh", "-c", "mkdir -p generated && touch generated/file"],
            tmp_path, profile="workspace-write", require_strong=True,
        )
    assert plan.strong and plan.backend == "broker"
    assert plan.argv[:3] == [
        __import__("sys").executable, "-m", "kitt.security.autopilot_broker"
    ]
    assert plan.env_overrides["KITT_SANDBOX_BROKER_SOCKET"].startswith("/run/")
    assert "/var/run/docker.sock" not in " ".join(plan.argv)


def test_broker_workspace_discovered_from_own_docker_mount():
    from types import SimpleNamespace
    from kitt.security.autopilot_broker import host_workspace_from_mount

    with patch("kitt.security.autopilot_broker.socket.gethostname", return_value="broker-id"), (
        patch("kitt.security.autopilot_broker.subprocess.run",
              return_value=SimpleNamespace(returncode=0, stdout=b"/home/user/project\\n"))
    ) as run:
        assert host_workspace_from_mount("/usr/bin/docker") == "/home/user/project"
        assert run.call_args.args[0][-1] == "broker-id"


def test_broker_rejects_missing_or_root_mount():
    from types import SimpleNamespace
    from kitt.security.autopilot_broker import host_workspace_from_mount
    for output in (b"", b"/", b"relative/path"):
        with patch("kitt.security.autopilot_broker.subprocess.run",
                   return_value=SimpleNamespace(returncode=0, stdout=output)):
            with pytest.raises(ValueError):
                host_workspace_from_mount()
