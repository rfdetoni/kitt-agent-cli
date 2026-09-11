from contextlib import closing
from pathlib import Path
import tempfile

from kitt.core.autonomy_policy import AutonomyPolicy
from kitt.tools.policy_engine import PolicyEngine
from kitt.tools.registry import ToolRegistry


def test_chain_policy_preserves_approval_and_denials():
    command = "mkdir -p pastaTeste2 && ls -ld pastaTeste2"
    for level, expected in [("supervised", "ASK"), ("autonomous", "ALLOW"), ("read_only", "DENY")]:
        policy = PolicyEngine(autonomy=AutonomyPolicy.preset(level))
        assert policy.evaluate_tool("run_command", {"command": command}) == expected
    for command in ["mkdir x && rm y", "mkdir x && curl example.com", "mkdir x &&", "mkdir x && echo $(id)", "mkdir x || ls", "mkdir x; ls"]:
        assert policy.evaluate_command(command) == "DENY"
    for level, expected in [("supervised", "ASK"), ("allow-all", "ALLOW")]:
        engine = PolicyEngine(autonomy=AutonomyPolicy.preset(level))
        assert engine.evaluate_tool("run_command", {"command": "rm example && echo done"}) == expected


def test_authorized_shell_chain_stops_on_failure():
    with tempfile.TemporaryDirectory() as root, closing(ToolRegistry(root_dir=root)) as registry:
        registry.policy.autonomy = AutonomyPolicy.preset("autonomous")
        result = registry.execute_tool("run_command", {"command": "mkdir -p pastaTeste2 && ls -ld pastaTeste2"})
        assert result.success, result.error
        assert (Path(root) / "pastaTeste2").is_dir()
        result = registry.execute_tool("run_command", {"command": "mkdir pastaTeste2 && mkdir must_not_exist"})
        assert not result.success
        assert not (Path(root) / "must_not_exist").exists()
        registry.policy.autonomy = AutonomyPolicy.preset("supervised")
        result = registry.execute_tool("run_command", {"command": "mkdir never_created && echo done"})
        assert result.requires_approval
        assert not (Path(root) / "never_created").exists()
