"""Wrapped Git mutations must not escape the protected .git sandbox mount."""
from __future__ import annotations

from types import SimpleNamespace

from kitt.runtime.core_runtime import SafeRuntime


def test_wrapped_git_erofs_gets_actionable_nonretryable_error():
    runtime = object.__new__(SafeRuntime)
    runtime.registry = SimpleNamespace(
        execute_tool=lambda *args, **kwargs: SimpleNamespace(
            success=False,
            requires_approval=False,
            error="Command exited with code 128",
            output=(
                '{"stderr":"fatal: Unable to create '
                "'/workspace/.git/index.lock': Sistema de arquivos somente para leitura"
                '"}'
            ),
            metadata={},
        ),
    )
    runtime.conversation_id = "conv"
    runtime.workspace_id = "workspace"
    result = runtime._op_registry_tool(
        "process.run", "run_command",
        {"argv": ["python3", "-c", "import subprocess; subprocess.run(['git','commit'])"]},
        "turn", "MODEL", None,
    )
    assert not result.success
    assert result.metadata["failure_kind"] == "GIT_METADATA_READ_ONLY_WRAPPED_PROCESS"
    assert result.metadata["retry_same_command"] is False
    assert "git.action" in result.error
    assert ".git/index.lock" in result.data
    assert "Command exited" not in result.error


def test_direct_git_error_is_not_relabelled_or_auto_approved():
    runtime = object.__new__(SafeRuntime)
    runtime.registry = SimpleNamespace(
        execute_tool=lambda *args, **kwargs: SimpleNamespace(
            success=False,
            requires_approval=False,
            error="Command exited with code 128",
            output="fatal: Unable to create /workspace/.git/index.lock: Read-only file system",
            metadata={},
        ),
    )
    runtime.conversation_id = "conv"
    runtime.workspace_id = "workspace"
    result = runtime._op_registry_tool(
        "git.action", "run_command",
        {"argv": ["git", "commit", "-m", "test"]},
        "turn", "MODEL", None,
    )
    assert not result.success
    assert result.error == "Command exited with code 128"
    assert "failure_kind" not in result.metadata
