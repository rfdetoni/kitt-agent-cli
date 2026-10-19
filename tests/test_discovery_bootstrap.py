"""Regressions from the 2026-10-19 discovery-loop diagnostics."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from kitt.context.query_plan import task_retrieval_text, QueryPlanner
from kitt.core.host_capabilities import capability_snapshot_for_prompt
from kitt.core.turn_processor import TurnProcessor
from kitt.re_source_stage import stage_wine
from kitt.vm_supervisor import VMError
from kitt.cli.main import build_parser


def test_generated_planner_contract_does_not_pollute_retrieval():
    prompt = ("Plan only: validation_prompt local_id Prefer Each "
              "[ORIGINAL REQUEST DATA]clone wine-staging and list stubs"
              "[END ORIGINAL REQUEST DATA]final kind depends_on")
    assert task_retrieval_text(prompt) == "clone wine-staging and list stubs"
    terms = QueryPlanner.plan(prompt).lexical_terms
    assert "validation_prompt" not in terms and "local_id" not in terms
    assert "wine" in terms or "wine-staging" in prompt


def test_goal_progress_scaffold_is_not_repository_query():
    prompt = ("Clone the Wine-Staging repository\n"
              "[KITT CONTRACT PROGRESS]\nT01:RUNNING FINAL:PENDING\n"
              "validation_prompt success_criteria")
    assert task_retrieval_text(prompt) == "Clone the Wine-Staging repository"


def test_re_snapshot_is_bounded_and_does_not_expand_authority(tmp_path):
    snapshot = capability_snapshot_for_prompt("in the guest linux kitt re list stubs", tmp_path)
    assert '"linux_network":"none"' in snapshot
    assert '"guest_git_push":false' in snapshot
    assert "stage-wine --approve" in snapshot
    assert "approval_granted" not in snapshot
    ordinary = capability_snapshot_for_prompt("write a Python unit test", tmp_path)
    assert '"re_lab":' not in ordinary


def test_reverse_proxy_tool_envelope_has_no_legacy_xml(tmp_path):
    obj = TurnProcessor.__new__(TurnProcessor)
    obj.session_state = SimpleNamespace(edit_strategy="search_replace")
    obj.registry = SimpleNamespace(get_tool_definitions=lambda _tools: [
        {"name": "kitt_runtime", "args": {"operation": {"type": "string"}}}
    ])
    instr = obj._tool_instructions(
        ["kitt_runtime"], planned_tools=["kitt_runtime"],
        agent_envelope=True, capability_snapshot="snapshot=test",
    )
    assert "action=use_tool" in instr
    assert "snapshot=test" in instr
    assert "<kitt-tool>" not in instr


def test_wine_stage_requires_explicit_operator_approval():
    with patch("kitt.re_source_stage.re_lab._root", side_effect=AssertionError("should not inspect")):
        with pytest.raises(VMError, match="--approve"):
            stage_wine()


def test_wine_stage_rejects_noninteractive_model_calls():
    with patch("kitt.re_source_stage.sys.stdin.isatty", return_value=False):
        with patch("kitt.re_source_stage.re_lab._root", side_effect=AssertionError("should not inspect")):
            with pytest.raises(VMError, match="interactive"):
                stage_wine(approve=True)


def test_wine_stage_parser():
    args = build_parser().parse_args(["re", "lab", "stage-wine", "--approve"])
    assert args.lab_action == "stage-wine" and args.approve is True
