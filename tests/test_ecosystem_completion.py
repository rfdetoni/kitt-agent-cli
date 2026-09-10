from __future__ import annotations

from pathlib import Path

import pytest

from kitt.children.backends import ChildAgentBackendRegistry
from kitt.cli.doctor import _check
from kitt.context_filter.instruction_selector import LazyInstructionSelector
from kitt.runtime.safe_runtime import OPERATION_SPECS


def test_instruction_selector_reports_considered_vs_injected(tmp_path: Path):
    rules = tmp_path / ".kitt" / "rules"
    rules.mkdir(parents=True)
    (rules / "base.md").write_text(
        "---\nname: base\nalwaysApply: true\n---\n# Base\nKeep changes small.\n",
        encoding="utf-8",
    )
    (rules / "python.md").write_text(
        "---\nname: python\ndepends_on: base\nkeywords: python\n---\n# Python\nUse Python safely.\n",
        encoding="utf-8",
    )
    selector = LazyInstructionSelector(tmp_path, max_total_chars=2048)
    selected = selector.select([], query="fix python module")
    assert [item.name for item in selected] == ["base", "python"]
    stats = selector.last_stats
    assert stats.considered_files == 2
    assert stats.injected_files == 2
    assert stats.dependency_edges == 1
    assert stats.considered_tokens >= stats.injected_tokens
    assert stats.budget_chars == 2048


def test_external_backends_are_explicitly_opt_in(tmp_path: Path):
    registry = ChildAgentBackendRegistry(enabled=False)
    assert "codex" in registry.names()
    with pytest.raises(PermissionError):
        registry.run("codex", "inspect only", root_dir=tmp_path, timeout_seconds=1)


def test_semantic_runtime_operations_are_registered():
    expected = {
        "repo.definition",
        "repo.hover",
        "repo.references_semantic",
        "repo.diagnostics",
        "repo.call_hierarchy",
        "repo.outline",
        "repo.ast_search",
        "security.scan",
    }
    assert expected.issubset(OPERATION_SPECS)


def test_doctor_check_exposes_lifecycle_state_and_legacy_status():
    result = _check("Example", "available", "ok")
    assert result["state"] == "AVAILABLE"
    assert result["status"] == "PASS"
