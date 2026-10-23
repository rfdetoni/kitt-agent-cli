"""test.run must select host recipes without accepting arbitrary commands."""
from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from kitt.runtime.core_runtime import SafeRuntime, OPERATION_REGISTRY
from kitt.security.capabilities import CAP_PROCESS_RUN


def test_host_recipe_selection_and_approved_execution_are_separate():
    assert OPERATION_REGISTRY["test.run"].required_capability == CAP_PROCESS_RUN
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "main.py").write_text("x = 1\n")
        tests = root / "tests"
        tests.mkdir()
        (tests / "test_main.py").write_text("def test_a(): assert True\n")
        runtime = object.__new__(SafeRuntime)
        runtime.root = root
        runtime._op_registry_tool = Mock(return_value=SimpleNamespace(success=True))
        scoped = SimpleNamespace(assert_path_allowed=Mock())

        preview = runtime._op_test_run({"paths": ["main.py"], "dry_run": True}, "t", "MODEL", scoped)
        assert preview.success
        assert preview.data["argv"] == ["python3", "-m", "pytest", "-q", "tests/test_main.py"]
        assert preview.data["network"] is False
        runtime._op_registry_tool.assert_not_called()

        executed = runtime._op_test_run({"paths": ["main.py"]}, "t", "MODEL", scoped)
        assert executed.success
        call = runtime._op_registry_tool.call_args
        assert call.args[0:2] == ("test.run", "run_command")
        assert call.args[2]["argv"] == preview.data["argv"]
        assert call.args[2]["network"] is False
        scoped.assert_path_allowed.assert_called_with("main.py")

        for bad in (
            {"paths": ["../outside.py"]},
            {"paths": ["/etc/passwd"]},
            {"paths": ["missing.py"]},
            {"paths": ["main.py"], "dry_run": 1},
            {"paths": ["main.py"], "argv": ["bash", "-c", "exit 1"]},
        ):
            assert not runtime._op_test_run(bad, "t", "MODEL", scoped).success
