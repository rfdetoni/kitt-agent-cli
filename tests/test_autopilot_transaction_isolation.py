"""Transaction runners must not fall back to bubblewrap's host root."""
from pathlib import Path
from unittest.mock import patch

import pytest

from kitt.security.execution_sandbox import (
    ExecutionSandbox, SandboxPlan, SandboxUnavailable,
)
from kitt.security.autopilot_broker import DockerBroker


def _task(tmp_path: Path) -> Path:
    root = tmp_path / ".autopilot-tasks" / ("a" * 32)
    root.mkdir(parents=True)
    return root


def test_task_requires_docker_even_if_bwrap_exists(tmp_path):
    root = _task(tmp_path)
    sandbox = ExecutionSandbox(root, auto_detect=False)
    with patch.object(sandbox, "_probe_broker", return_value=None), (
        patch.object(sandbox, "_probe_docker", return_value=None)
    ), patch.object(sandbox, "_probe_bwrap", return_value=("/usr/bin/bwrap", (0, 13, 0))):
        with pytest.raises(SandboxUnavailable, match="require Docker"):
            sandbox.plan(["sh", "-c", "true"], root,
                         profile="workspace-write", require_strong=True)


def test_task_docker_mount_uses_only_task_root(tmp_path):
    root = _task(tmp_path)
    sandbox = ExecutionSandbox(root, auto_detect=False)
    planned = SandboxPlan(["docker", "run"], "workspace-write", "docker",
                          True, True, root)
    with patch.object(sandbox, "_probe_broker", return_value=None), (
        patch.object(sandbox, "_probe_docker", return_value=("docker", "alpine:3.20"))
    ), patch.object(sandbox, "_docker_plan", return_value=planned) as prepare:
        assert sandbox.plan(["sh", "-c", "true"], root,
                            profile="workspace-write", require_strong=True) is planned
    assert prepare.call_args.args[-1] == root


def test_broker_restricts_task_subdirectory(tmp_path):
    root = tmp_path / "workspace"
    task = root / ".autopilot-tasks" / ("a" * 32)
    task.mkdir(parents=True)
    (task / ".git").write_text("gitdir: /workspace/.git/worktrees/id")
    broker = DockerBroker.__new__(DockerBroker)
    broker.root = root
    assert broker._task_root(f".autopilot-tasks/{'a' * 32}") == task
    for value in ("../secret", ".autopilot-tasks/../secret", "/etc"):
        with pytest.raises(PermissionError):
            broker._task_root(value)
