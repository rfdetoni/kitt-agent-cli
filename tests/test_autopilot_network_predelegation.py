"""Only an explicit operator opt-in grants network to a root principal."""
from kitt.security.capabilities import CAP_NETWORK_ACCESS, CAP_REPO_READ
from kitt.security.context import ExecutionSecurityContext


def test_operator_network_predelegation_is_root_only(monkeypatch):
    monkeypatch.setenv("KITT_AUTOPILOT_DELEGATE_NETWORK", "true")
    root = ExecutionSecurityContext.create_user_context(
        "w", "c", capabilities=[CAP_REPO_READ],
    )
    scoped = ExecutionSecurityContext.create_user_context(
        "w", "c", capabilities=[CAP_REPO_READ], path_scope=["src"],
    )
    assert CAP_NETWORK_ACCESS in root.capabilities
    assert CAP_NETWORK_ACCESS not in scoped.capabilities


def test_network_denied_without_operator_opt_in(monkeypatch):
    monkeypatch.delenv("KITT_AUTOPILOT_DELEGATE_NETWORK", raising=False)
    root = ExecutionSecurityContext.create_user_context(
        "w", "c", capabilities=[CAP_REPO_READ],
    )
    assert CAP_NETWORK_ACCESS not in root.capabilities
