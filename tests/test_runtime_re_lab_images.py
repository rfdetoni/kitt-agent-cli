"""Model-facing read-only image diagnosis must never receive Docker socket."""
from __future__ import annotations

from types import SimpleNamespace

from kitt import re_image_install
from kitt.runtime.core_runtime import SafeRuntime, OPERATION_REGISTRY
from kitt.security.capabilities import CAP_REPO_READ


def test_re_lab_images_is_read_only_repo_capability(monkeypatch):
    assert OPERATION_REGISTRY["re.lab.images"].required_capability == CAP_REPO_READ
    assert OPERATION_REGISTRY["re.lab.images"].sensitive is False
    runtime = object.__new__(SafeRuntime)
    marker = {"status": "READY", "images": {
        "ghidra": {"status": "READY"}, "frida": {"status": "READY"},
        "full": {"status": "READY"},
    }}
    calls = []
    monkeypatch.setattr(re_image_install, "status",
                        lambda **kw: (calls.append(kw) or marker))
    context = SimpleNamespace(principal_type="agent")
    result = runtime._op_re_lab_images({}, context)
    assert result.success and result.data is marker
    assert calls == [{}]
    assert not runtime._op_re_lab_images({"argv": ["docker", "ps"]}, context).success
    assert not runtime._op_re_lab_images({}, None).success


def test_re_lab_images_exposes_blocker_without_mutating(monkeypatch):
    runtime = object.__new__(SafeRuntime)
    monkeypatch.setattr(re_image_install, "status",
                        lambda **kw: {"status": "BLOCKED_ENV", "images": {},
                                      "reason": "Host Docker unavailable"})
    result = runtime._op_re_lab_images({}, SimpleNamespace(principal_type="agent"))
    assert not result.success
    assert "Docker" in result.data["reason"]



def test_re_lab_analyze_has_sensitive_process_capability_and_fixed_policy():
    from kitt.security.capabilities import CAP_PROCESS_RUN
    spec = OPERATION_REGISTRY["re.lab.analyze"]
    assert spec.required_capability == CAP_PROCESS_RUN
    assert spec.sensitive and spec.risk_cost >= 3
    assert spec.policy_tool_action == "re_lab_analyze"
    assert spec.resume_tool_name is None


def test_re_lab_analyze_uses_host_owned_executor_not_sandbox_shell(
    monkeypatch, tmp_path
):
    from kitt import re_workspace
    from unittest.mock import Mock

    calls = []
    fake_result = {"status": "PLAN_ONLY", "source": "2.1.308"}

    def fake_analyze(**kw):
        calls.append(kw)
        return fake_result

    monkeypatch.setattr(re_workspace, "analyze_workspace", fake_analyze)
    runtime = object.__new__(SafeRuntime)
    runtime.root = tmp_path
    context = SimpleNamespace(is_path_scoped=False, assert_path_allowed=Mock())
    result = runtime._op_re_lab_analyze(
        {"path": "2.1.308", "dry_run": True}, context,
    )
    assert result.success and result.data is fake_result
    assert calls == [{"workspace": tmp_path, "path": "2.1.308",
                      "resume_job": None, "dry_run": True}]
    context.assert_path_allowed.assert_called_once_with("2.1.308")
    assert not runtime._op_re_lab_analyze({"argv": ["docker", "ps"]}, context).success
    assert not runtime._op_re_lab_analyze({"path": "foo", "dry_run": 1}, context).success
    child = SimpleNamespace(is_path_scoped=True)
    assert not runtime._op_re_lab_analyze({"path": "2.1.308"}, child).success
    assert len(calls) == 1


def test_re_lab_analyze_returns_partial_failure_with_diagnostics(monkeypatch, tmp_path):
    from kitt import re_workspace
    monkeypatch.setattr(re_workspace, "analyze_workspace",
                        lambda **kw: {"status": "ANALYSIS_TIMED_OUT"})
    runtime = object.__new__(SafeRuntime)
    runtime.root = tmp_path
    context = SimpleNamespace(is_path_scoped=False,
                              assert_path_allowed=lambda path: None)
    result = runtime._op_re_lab_analyze({"path": "2.1.308"}, context)
    assert not result.success
    assert "ANALYSIS_TIMED_OUT" in result.error
    assert result.data["status"] == "ANALYSIS_TIMED_OUT"

def test_docker_socket_failure_in_agent_sandbox_routes_to_governed_re_tools():
    runtime = object.__new__(SafeRuntime)
    runtime.conversation_id = "session"
    runtime.workspace_id = "workspace"
    output = ("failed to connect to the docker API at "
              "unix:///var/run/docker.sock; permission denied")
    runtime.registry = SimpleNamespace(
        execute_tool=lambda *args, **kwargs: SimpleNamespace(
            success=False, output=output, error="process failed",
            metadata={}, requires_approval=False),
    )
    result = runtime._op_registry_tool(
        "process.run", "run_command", {"argv": ["docker", "ps"]},
        "turn", "MODEL", None,
    )
    assert not result.success
    assert "re.lab.images" in result.error
    assert "re.lab.analyze" in result.error
    assert result.metadata["failure_kind"] == "DOCKER_SANDBOX_SOCKET_UNAVAILABLE"
    assert result.metadata["retry_same_command"] is False
    assert result.data == output


def test_docker_socket_guidance_does_not_rewrite_unrelated_failures():
    runtime = object.__new__(SafeRuntime)
    runtime.conversation_id = "session"
    runtime.workspace_id = "workspace"
    runtime.registry = SimpleNamespace(
        execute_tool=lambda *args, **kwargs: SimpleNamespace(
            success=False, output="file not found", error="unrelated failure",
            metadata={}, requires_approval=False),
    )
    result = runtime._op_registry_tool(
        "process.run", "run_command", {"argv": ["docker", "build", "."]},
        "turn", "MODEL", None,
    )
    assert result.error == "unrelated failure"
    assert "failure_kind" not in result.metadata
