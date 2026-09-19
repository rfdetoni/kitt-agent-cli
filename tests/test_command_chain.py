from contextlib import closing
from pathlib import Path
import sys
import tempfile

from kitt.core.autonomy_policy import AutonomyPolicy
from kitt.tools.policy_engine import PolicyEngine
from kitt.tools.registry import ToolRegistry


def test_process_policy_is_argv_only_and_shell_contracts_are_denied():
    argv = [sys.executable, "-c", "print('ok')"]
    for level, expected in [
        ("supervised", "ASK"),
        ("autonomous", "ALLOW"),
        ("read_only", "DENY"),
    ]:
        policy = PolicyEngine(autonomy=AutonomyPolicy.preset(level))
        assert policy.evaluate_tool("run_command", {"argv": argv}) == expected
        assert policy.evaluate_tool(
            "run_command",
            {"command": "mkdir -p pastaTeste2 && ls -ld pastaTeste2"},
        ) == "DENY"

    policy = PolicyEngine()
    for command in [
        "mkdir x && rm y",
        "mkdir x && curl example.com",
        "mkdir x &&",
        "mkdir x && echo $(id)",
        "mkdir x || ls",
        "mkdir x; ls",
    ]:
        assert policy.evaluate_command(command) == "DENY"


def test_runtime_executes_direct_argv_and_rejects_shell_escape():
    with tempfile.TemporaryDirectory() as root, closing(ToolRegistry(root_dir=root)) as registry:
        registry.policy.autonomy = AutonomyPolicy.preset("autonomous")

        result = registry.execute_tool(
            "run_command",
            {
                "argv": [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path('created').mkdir()",
                ]
            },
        )
        if registry.process_runner.sandbox.is_strong_available():
            assert result.success, result.error
            assert (Path(root) / "created").is_dir()
            assert result.metadata["sandbox"]["strong"]
        else:
            assert result.requires_approval
            assert not (Path(root) / "created").exists()

        result = registry.execute_tool(
            "run_command",
            {"argv": ["sh", "-c", "mkdir must_not_exist && echo done"]},
        )
        assert not result.success
        assert not (Path(root) / "must_not_exist").exists()

        registry.policy.autonomy = AutonomyPolicy.preset("supervised")
        result = registry.execute_tool(
            "run_command",
            {
                "argv": [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path('never_created').mkdir()",
                ]
            },
        )
        assert result.requires_approval
        assert not (Path(root) / "never_created").exists()
