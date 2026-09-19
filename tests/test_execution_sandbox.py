from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

from kitt.security.execution_sandbox import (
    ExecutionSandbox,
    SandboxUnavailable,
    query_landlock_abi,
)
from kitt.tools.process_runner import ProcessRunner


def test_landlock_plan_is_strong_and_uses_standalone_launcher():
    with tempfile.TemporaryDirectory() as root:
        sandbox = ExecutionSandbox(root, auto_detect=False, landlock_abi=3)
        plan = sandbox.plan(
            [sys.executable, "-c", "print('ok')"],
            root,
            profile="workspace-write+network",
            require_strong=True,
        )
        try:
            assert plan.strong
            assert plan.backend == "landlock"
            assert plan.argv[1] == "-I"
            assert "landlock_exec.py" in plan.argv[2]
            assert plan.network_isolated is False
        finally:
            sandbox.cleanup(plan)


def test_no_network_profile_fails_closed_without_namespace_backend():
    with tempfile.TemporaryDirectory() as root:
        sandbox = ExecutionSandbox(root, auto_detect=False, landlock_abi=3)
        with pytest.raises(SandboxUnavailable):
            sandbox.plan(
                [sys.executable, "-c", "print('ok')"],
                root,
                profile="workspace-write",
                require_strong=True,
            )


def test_bubblewrap_plan_uses_namespaces_and_network_isolation():
    with tempfile.TemporaryDirectory() as root:
        fake = Path(root).parent / "kitt-fake-bwrap"
        fake.write_text("", encoding="utf-8")
        try:
            sandbox = ExecutionSandbox(
                root,
                bwrap_path=str(fake),
                bwrap_version=(0, 12, 0),
                landlock_abi=0,
                auto_detect=False,
            )
            plan = sandbox.plan(
                ["tool", "--version"],
                root,
                profile="workspace-write",
                require_strong=True,
            )
            try:
                assert plan.backend == "bubblewrap"
                assert plan.strong
                assert plan.network_isolated
                assert "--unshare-net" in plan.argv
                assert "--ro-bind" in plan.argv
                assert "--bind" in plan.argv
                assert "--new-session" in plan.argv
            finally:
                sandbox.cleanup(plan)
        finally:
            fake.unlink(missing_ok=True)


@pytest.mark.skipif(query_landlock_abi() <= 0, reason="Landlock unavailable")
def test_landlock_process_cannot_write_outside_workspace():
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
        sandbox = ExecutionSandbox(
            root,
            auto_detect=False,
            landlock_abi=query_landlock_abi(),
        )
        runner = ProcessRunner(root, sandbox=sandbox)
        outside_file = Path(outside) / "escape.txt"
        script = (
            "from pathlib import Path\n"
            f"outside = Path({str(outside_file)!r})\n"
            "try:\n"
            "    outside.write_text('escape', encoding='utf-8')\n"
            "except OSError:\n"
            "    pass\n"
            "Path('inside.txt').write_text('inside', encoding='utf-8')\n"
        )
        result = runner.run(
            [sys.executable, "-c", script],
            sandbox_profile="workspace-write+network",
            require_strong_sandbox=True,
        )
        assert result.returncode == 0, result.stderr
        assert result.sandbox_backend == "landlock"
        assert result.sandbox_strong
        assert (Path(root) / "inside.txt").read_text(encoding="utf-8") == "inside"
        assert not outside_file.exists()
