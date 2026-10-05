from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]

RELEASE_CRITICAL_TESTS = (
    "tests/test_smoke.py",
    "tests/test_execution_budget.py",
    "tests/test_run_coordinator.py",
    "tests/test_turn_processor_decomposition.py",
    "tests/test_event_ledger_replay.py",
    "tests/test_completion_contract.py",
    "tests/test_verification_contract.py",
    "tests/test_tool_payload_contract.py",
    "tests/test_tool_registry.py",
    "tests/test_tool_result_evidence.py",
    "tests/test_provider_auth_contract.py",
    "tests/test_provider_catalog_contract.py",
    "tests/test_provider_runtime_contract.py",
    "tests/test_kitt_reverse_proxy_compat.py",
    "tests/test_cancellation_real_stop.py",
    "tests/test_approvals_cas_atomic.py",
    "tests/test_autonomy_policy.py",
    "tests/test_security_and_policies.py",
    "tests/test_credentials_security.py",
    "tests/test_secret_observability_boundaries.py",
    "tests/test_safe_runtime_approval_delegation.py",
    "tests/test_extension_security.py",
    "tests/test_installer_service_shutdown.py",
    "tests/test_runtime_resilience.py",
    "tests/test_retry_budget_accounting.py",
    "tests/test_prompt_no_duplication.py",
    "tests/test_required_workspace_mutation_guard.py",
    "tests/test_children_events.py",
    "tests/test_child_lifecycle_guards.py",
    "tests/test_tui_behavioral.py",
    "tests/test_cli_banner_version.py",
    "tests/test_continuous_agent_runtime.py",
    "tests/test_agentic_remaining_acceptance.py",
    "tests/test_learn_privacy_acceptance.py",
    "tests/test_reverse_proxy_predispatch.py",
    "tests/test_task_plan_child_manager.py",
    "tests/memory/test_memory_manager_shared.py",
    "tests/memory/test_shared_memory_client.py",
    "tests/native/test_kitt_integration_contract.py",
)


def main() -> int:
    missing = [path for path in RELEASE_CRITICAL_TESTS if not (ROOT / path).is_file()]
    if missing:
        raise SystemExit(f"release-critical test paths are missing: {missing}")
    return int(pytest.main(["-q", *RELEASE_CRITICAL_TESTS]))


if __name__ == "__main__":
    raise SystemExit(main())
