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


def test_landlock_plan_is_partial_and_never_satisfies_strong_requirement():
    with tempfile.TemporaryDirectory() as root:
        sandbox = ExecutionSandbox(root, auto_detect=False, landlock_abi=3)
        with pytest.raises(SandboxUnavailable):
            sandbox.plan(
                [sys.executable, "-c", "print('ok')"],
                root,
                profile="workspace-write+network",
                require_strong=True,
            )

        plan = sandbox.plan(
            [sys.executable, "-c", "print('ok')"],
            root,
            profile="workspace-write+network",
            require_strong=False,
        )
        try:
            assert not plan.strong
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


def test_bubblewrap_plan_uses_namespaces_and_masks_control_plane_secrets():
    with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as home:
        root_path = Path(root)
        home_path = Path(home)
        (root_path / ".git").mkdir()
        (root_path / ".kitt").mkdir()
        (home_path / ".ssh").mkdir()
        (home_path / ".git-credentials").write_text("secret", encoding="utf-8")
        fake = home_path / "kitt-fake-bwrap"
        fake.write_text("", encoding="utf-8")

        sandbox = ExecutionSandbox(
            root,
            bwrap_path=str(fake),
            bwrap_version=(0, 12, 0),
            landlock_abi=0,
            auto_detect=False,
            home_dir=home,
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
            assert "--new-session" in plan.argv

            def has_sequence(*sequence: str) -> bool:
                width = len(sequence)
                return any(
                    plan.argv[index:index + width] == list(sequence)
                    for index in range(len(plan.argv) - width + 1)
                )

            canonical_root = sandbox.root
            canonical_home = sandbox.home
            assert has_sequence(
                "--ro-bind",
                str(canonical_root / ".git"),
                str(canonical_root / ".git"),
            )
            assert has_sequence(
                "--ro-bind",
                str(canonical_root / ".kitt"),
                str(canonical_root / ".kitt"),
            )
            assert has_sequence("--tmpfs", str(canonical_home / ".ssh"))
            assert has_sequence(
                "--ro-bind",
                "/dev/null",
                str(canonical_home / ".git-credentials"),
            )
        finally:
            sandbox.cleanup(plan)


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
            require_strong_sandbox=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.sandbox_backend == "landlock"
        assert not result.sandbox_strong
        assert (Path(root) / "inside.txt").read_text(encoding="utf-8") == "inside"
        assert not outside_file.exists()
