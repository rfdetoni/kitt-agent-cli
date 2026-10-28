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


def test_autonomous_build_prefers_docker_when_bubblewrap_is_present(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    sandbox = ExecutionSandbox(root, auto_detect=False)
    with patch.object(sandbox, "_probe_bwrap", return_value=("/usr/bin/bwrap", (0, 12, 0))), (
        patch.object(sandbox, "_probe_docker", return_value=("/usr/bin/docker", "prepared:image"))
    ), patch.object(sandbox, "_ensure_toolchain_image"), (
        patch.dict("os.environ", {"KITT_AUTOPILOT_IMAGE": ""})
    ):
        build = sandbox.plan(
            ["mvn", "test"], root, profile="workspace-write",
            require_strong=True, prefer_docker_toolchain=True,
        )
        host = sandbox.plan(
            ["mvn", "test"], root, profile="workspace-write",
            require_strong=True,
        )
    assert build.backend == "docker"
    assert "maven:3.9-eclipse-temurin-25-noble" in build.argv
    assert host.backend == "bubblewrap"


def test_generic_autonomous_command_stays_on_bubblewrap(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "pom.xml").write_text("<project/>")
    sandbox = ExecutionSandbox(root, auto_detect=False)
    with patch.object(sandbox, "_probe_bwrap", return_value=("/usr/bin/bwrap", (0, 12, 0))), (
        patch.object(sandbox, "_probe_docker", return_value=("/usr/bin/docker", "prepared:image"))
    ), patch.dict("os.environ", {"KITT_AUTOPILOT_IMAGE": ""}):
        plan = sandbox.plan(
            ["git", "status"], root, profile="workspace-write",
            require_strong=True, prefer_docker_toolchain=True,
        )
    assert plan.backend == "bubblewrap"


def test_sandbox_container_errors_are_actionable_without_exposing_host_socket():
    from types import SimpleNamespace
    from kitt.runtime.core_runtime import SafeRuntime

    class FakeRunner:
        def __init__(self):
            self.stderr = ""

        def run(self, argv, **kwargs):
            return SimpleNamespace(
                stdout="", stderr=self.stderr, returncode=1,
                timed_out=False, cancelled=False, truncated=False,
                sandbox_backend="bubblewrap",
            )

    runner = FakeRunner()
    runtime = SimpleNamespace(
        registry=SimpleNamespace(process_runner=runner),
        autopilot_worktree=SimpleNamespace(current=lambda: None),
    )
    cases = (
        ("docker", "failed to connect to the docker API at unix:///var/run/docker.sock",
         "DOCKER_SANDBOX_SOCKET_UNAVAILABLE"),
        ("podman", "bwrap: execvp podman: No such file or directory",
         "PODMAN_SANDBOX_EXECUTABLE_UNAVAILABLE"),
        ("find", "find: '/opt/containerd': Permission denied",
         "HOST_PATH_SEARCH_DENIED"),
    )
    for command, stderr, expected in cases:
        runner.stderr = stderr
        result = SafeRuntime._op_autonomous_sandbox_run(
            runtime, {"argv": [command, "--version"]}, "turn", None,
        )
        assert not result.success
        assert result.metadata["failure_kind"] == expected
        assert result.metadata["retry_same_command"] is False
        assert "do not" in (result.error or "").lower()
        assert len(result.data["stderr"]) > 0
