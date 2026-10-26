"""Regression coverage for the strong, no-host-shell autonomous executor."""
from pathlib import Path
from unittest.mock import patch

from kitt.security.execution_sandbox import ExecutionSandbox


def test_docker_fallback_is_confined(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".kitt").mkdir()
    (root / "sub").mkdir()
    sandbox = ExecutionSandbox(root, auto_detect=False)
    with patch.object(sandbox, "_probe_bwrap", return_value=None), (
        patch.object(sandbox, "_probe_docker", return_value=("/usr/bin/docker", "prepared:image"))
    ):
        assert sandbox.is_strong_available("workspace-write")
        plan = sandbox.plan(
            ["sh", "-c", "mkdir -p nested && rm -rf other"],
            root / "sub", profile="workspace-write", require_strong=True,
        )
    assert plan.strong and plan.backend == "docker" and plan.network_isolated
    assert plan.argv[-3:] == ["sh", "-c", "mkdir -p nested && rm -rf other"]
    assert "--cap-drop=ALL" in plan.argv
    assert "--security-opt=no-new-privileges" in plan.argv
    assert "--network" in plan.argv and plan.argv[plan.argv.index("--network") + 1] == "none"
    assert "type=tmpfs,dst=/workspace/.kitt" in plan.argv
    assert "/var/run/docker.sock" not in " ".join(plan.argv)
    assert "--workdir" in plan.argv and plan.argv[plan.argv.index("--workdir") + 1] == "/workspace/sub"


def test_docker_fallback_read_only_mount(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    sandbox = ExecutionSandbox(root, auto_detect=False)
    with patch.object(sandbox, "_probe_bwrap", return_value=None), (
        patch.object(sandbox, "_probe_docker", return_value=("/usr/bin/docker", "prepared:image"))
    ):
        plan = sandbox.plan(["pwd"], root, profile="read-only", require_strong=True)
    assert any(x.startswith("type=bind,") and x.endswith(",readonly") for x in plan.argv)
