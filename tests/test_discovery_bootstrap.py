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


def test_discovery_classifier_excludes_real_mutation():
    from kitt.core.turn_tool_loop import _is_environment_discovery as discovery

    def args(*argv):
        return {"operation": "process.run", "arguments": {"argv": list(argv)}}

    assert discovery("process.run", args("git", "status", "--short"))
    assert discovery("process.run", args("kitt", "re", "lab", "status"))
    assert discovery("repo.list", {"operation": "repo.list", "arguments": {"path": "."}})
    assert not discovery("process.run", args("git", "add", "."))
    assert not discovery("process.run", args("git", "commit", "-m", "fix"))
    assert not discovery("process.run", args("docker", "exec", "kitt-re-full", "test"))
    assert not discovery("process.run", {"operation": "process.run", "arguments": None})


def test_wine_stage_clones_official_source_in_shared_workspace(tmp_path, monkeypatch):
    from kitt import re_source_stage as source
    lab = tmp_path / "re-lab"
    ws = lab / "workspace"
    ws.mkdir(parents=True)
    monkeypatch.setattr(source.re_lab, "_root", lambda _: lab)
    monkeypatch.setattr(source.re_docker_lab, "inspect", lambda role: {
        "status": "running", "workspace": str(ws)
    })
    monkeypatch.setattr(source.os, "getuid", lambda: ws.stat().st_uid)
    monkeypatch.setattr(source.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(source.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(source.shutil, "which", lambda _: "/usr/bin/git")
    calls = []
    def git(argv, **kwargs):
        calls.append(argv)
        if argv[1] == "clone":
            dest = Path(argv[-1])
            dest.mkdir()
            (dest / "README.md").write_text("source")
            return SimpleNamespace(returncode=0)
        return SimpleNamespace(returncode=0, stdout="a" * 40 + "\n")

    monkeypatch.setattr(source.subprocess, "run", git)
    result = source.stage_wine(approve=True)
    assert result["status"] == "STAGED"
    assert result["commit"] == "a" * 40
    assert result["guest_path"] == "/workspace/sources/wine-staging"
    assert (ws / "sources" / "wine-staging" / "README.md").read_text() == "source"
    assert calls[0][1:5] == ["clone", "--depth", "1", "--"]
    assert source.OFFICIAL_WINE_STAGING in calls[0]
    assert result["guest_network_enabled"] is False
    with pytest.raises(VMError, match="already exists"):
        source.stage_wine(approve=True)
    assert len(calls) == 2


def test_single_file_goal_uses_local_planner_without_llm(tmp_path):
    from kitt.goals.contract import ContractPlanner
    from kitt.security.workspace_fs import WorkspaceFileSystem
    planner = ContractPlanner.__new__(ContractPlanner)
    planner.fs = WorkspaceFileSystem(tmp_path)
    planner.known_check_ids = []
    items = planner._simple_single_file_plan("Atualize README.md corrigindo um título")
    assert len(items) == 2
    assert items[0]["paths"] == ["README.md"]
    assert items[-1]["kind"] == "final"
    assert planner._simple_single_file_plan(
        "Clone Wine Staging e gere um inventário de DLLs"
    ) is None
    assert planner._simple_single_file_plan(
        "Atualize README.md e CHANGELOG.md"
    ) is None
