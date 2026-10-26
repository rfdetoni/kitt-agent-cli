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


def test_host_run_stays_unchanged_without_automatic_sandbox(tmp_path: Path):
    from types import SimpleNamespace
    from kitt.runtime.core_runtime import SafeRuntime
    from kitt.security.capabilities import CAP_PROCESS_RUN, CAP_REPO_WRITE
    from kitt.core.autonomy_policy import AutonomyPolicy

    class Sandbox:
        def is_strong_available(self, _profile):
            return False

    class Principal:
        capabilities = {CAP_PROCESS_RUN, CAP_REPO_WRITE}
        is_path_scoped = False

        def assert_scope(self, workspace, conversation):
            assert workspace == "workspace" and conversation == "conversation"

    root = tmp_path / "workspace"
    root.mkdir()
    registry = SimpleNamespace(
        process_runner=SimpleNamespace(sandbox=Sandbox()),
        policy=SimpleNamespace(autonomy=AutonomyPolicy.preset("autonomous")),
    )
    runtime = SafeRuntime(root, "workspace", "conversation", tool_registry=registry)
    result = runtime.execute(
        "sandbox.run", {"argv": ["sh", "-c", "true"]},
        security_context=Principal(),
    )
    assert not result.success
    assert "sandbox" in (result.error or "").lower()


def test_numeric_network_flag_is_not_accepted_without_network_capability(tmp_path: Path):
    from types import SimpleNamespace
    from kitt.runtime.core_runtime import SafeRuntime
    from kitt.security.capabilities import CAP_PROCESS_RUN, CAP_REPO_WRITE
    from kitt.core.autonomy_policy import AutonomyPolicy

    class Sandbox:
        def is_strong_available(self, _profile):
            return True

    class Principal:
        capabilities = {CAP_PROCESS_RUN, CAP_REPO_WRITE}
        is_path_scoped = False

        def assert_scope(self, workspace, conversation):
            assert workspace == "workspace" and conversation == "conversation"

    root = tmp_path / "workspace"
    root.mkdir()
    registry = SimpleNamespace(
        process_runner=SimpleNamespace(sandbox=Sandbox()),
        policy=SimpleNamespace(autonomy=AutonomyPolicy.preset("autonomous")),
    )
    runtime = SafeRuntime(root, "workspace", "conversation", tool_registry=registry)
    for operation in ("sandbox.run", "process.run"):
        for numeric in (1, 0):
            result = runtime.execute(
                operation, {"argv": ["sh", "-c", "true"], "network": numeric},
                security_context=Principal(),
            )
            assert not result.success
            assert "network must be a boolean" in (result.error or "")


def test_fixed_language_toolchain_selection(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "pom.xml").write_text("<project/>")
    frontend = root / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text("{}")
    rust = root / "rust"
    rust.mkdir()
    (rust / "Cargo.toml").write_text("[package]\\nname='demo'")
    choose = ExecutionSandbox._select_toolchain_image
    with patch.dict("os.environ", {"KITT_AUTOPILOT_IMAGE": ""}):
        assert choose("prepared:image", ["bash", "-lc", "./mvnw test"], root) == (
            "maven:3.9-eclipse-temurin-25-noble"
        )
        assert choose("prepared:image", ["npm", "test"], frontend) == "node:24-bookworm"
        assert choose("prepared:image", ["cargo", "test"], rust) == "rust:1.99-bookworm"
        assert choose("prepared:image", ["echo", "hi"], root) == "prepared:image"
        assert choose("prepared:image", ["git", "status"], root) == "prepared:image"
        assert choose("prepared:image", ["bash", "-lc", "git status"], root) == "prepared:image"
        assert choose("prepared:image", ["./gradlew", "test"], root) == "gradle:9-jdk25-noble"
        assert choose("prepared:image", ["bash", "-lc", "./gradlew test"], root) == (
            "gradle:9-jdk25-noble"
        )
    with patch.dict("os.environ", {"KITT_AUTOPILOT_IMAGE": "custom:trusted"}):
        assert choose("custom:trusted", ["npm", "test"], frontend) == "custom:trusted"


def test_docker_toolchain_provisioning_uses_only_fixed_images(tmp_path: Path):
    from types import SimpleNamespace
    from kitt.security.execution_sandbox import SandboxUnavailable

    sandbox = ExecutionSandbox(tmp_path, auto_detect=False)
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=1 if "inspect" in argv else 0, stderr=b"")

    with patch("kitt.security.execution_sandbox.subprocess.run", side_effect=run):
        sandbox._ensure_toolchain_image("/usr/bin/docker", "node:24-bookworm")
        sandbox._ensure_toolchain_image("/usr/bin/docker", "node:24-bookworm")
    assert len(calls) == 2
    assert calls[-1] == ["/usr/bin/docker", "pull", "node:24-bookworm"]
    with patch("kitt.security.execution_sandbox.subprocess.run") as runner:
        try:
            sandbox._ensure_toolchain_image("/usr/bin/docker", "malicious/unknown:latest")
            assert False, "Expected fail-closed"
        except SandboxUnavailable:
            runner.assert_not_called()
