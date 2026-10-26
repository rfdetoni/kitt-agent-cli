"""Exercise real autonomous commands inside Docker's workspace boundary."""
from __future__ import annotations
import os
import shutil
from pathlib import Path
from unittest.mock import patch
import pytest
from kitt.security.execution_sandbox import ExecutionSandbox
from kitt.tools.process_runner import ProcessRunner


@pytest.mark.skipif(
    os.environ.get("KITT_CONTAINER_RUNTIME") != "docker",
    reason="requires the Docker integration runner",
)
def test_autonomous_docker_workspace_and_readonly(tmp_path: Path):
    docker = shutil.which("docker")
    assert docker is not None
    root = tmp_path / "workspace"
    root.mkdir()
    (root / ".kitt").mkdir()
    (root / ".kitt" / "control_secret").write_text("HOST_ONLY")
    (root / "pom.xml").write_text("<project/>")

    sandbox = ExecutionSandbox(root, auto_detect=False)
    runner = ProcessRunner(str(root), sandbox=sandbox)
    with patch.object(sandbox, "_probe_bwrap", return_value=None), patch.object(
        sandbox, "_probe_docker", return_value=(docker, "alpine:3.20")
    ):
        result = runner.run(
            ["sh", "-c", "mkdir -p generated && printf ready > generated/status "
             "&& test ! -e .kitt/control_secret && test ! -e /var/run/docker.sock"],
            sandbox_profile="workspace-write", require_strong_sandbox=True,
        )
        assert result.returncode == 0, result.stderr
        assert result.sandbox_backend == "docker" and result.sandbox_strong
        assert result.sandbox_network_isolated
        assert (root / "generated" / "status").read_text() == "ready"

        readonly = runner.run(
            ["sh", "-c", "printf bad > generated/forbidden"],
            sandbox_profile="read-only", require_strong_sandbox=True,
        )
        assert readonly.returncode != 0
        assert not (root / "generated" / "forbidden").exists()
        assert (root / ".kitt" / "control_secret").read_text() == "HOST_ONLY"
