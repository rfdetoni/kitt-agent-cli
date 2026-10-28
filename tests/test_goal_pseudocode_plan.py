from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from kitt.goals.contract import ContractPlanner


def _item(local_id, *, kind="task", title=None, prompt=None, validation=None,
          paths=None, depends_on=None, criteria=None):
    return {
        "local_id": local_id, "kind": kind,
        "title": title or f"Task {local_id}",
        "prompt": prompt or f"Implement task {local_id}.",
        "validation_prompt": validation or f"Verify task {local_id}.",
        "success_criteria": criteria or [f"{local_id} verified"],
        "check_ids": [], "paths": paths or [], "depends_on": depends_on or [],
    }


def test_non_pseudoc_request_does_not_trigger_pseudoc_deliverable_guard(tmp_path):
    planner = ContractPlanner(SimpleNamespace(canonical_root=Path(tmp_path)))
    payload = {"items": [
        _item("T01", paths=["analysis/inventory.json"]),
        _item("FINAL", kind="final", depends_on=["T01"]),
    ]}
    result = planner.validate(payload, original_prompt="Review the static ELF inventory.")
    assert result[-1]["kind"] == "final"


def test_simple_single_file_shortcut_does_not_bypass_pseudoc_plan(monkeypatch, tmp_path):
    planner = ContractPlanner(SimpleNamespace(canonical_root=Path(tmp_path)))
    shortcut = [
        {
            "local_id": "T01", "kind": "task", "title": "Inspect ELF",
            "prompt": "Inspect ELF headers only.", "validation_prompt": "Verify inventory.",
            "success_criteria": ["Inventory exists"], "check_ids": [],
            "paths": ["analysis/inventory.json"], "depends_on": [],
        },
        {
            "local_id": "FINAL", "kind": "final", "title": "Finish",
            "prompt": "Confirm inventory exists.", "validation_prompt": "Verify inventory.",
            "success_criteria": ["Inventory exists"], "check_ids": [],
            "paths": ["analysis/inventory.json"], "depends_on": ["T01"],
        },
    ]
    monkeypatch.setattr(planner, "_simple_single_file_plan", lambda prompt: shortcut)
    progress = next(planner.iter_plan("conversation_test", "Create pseudoC for 2.1.308."))
    assert progress.phase == "generating"


def test_planner_rejects_inventory_only_plan_for_explicit_pseudoc_request(tmp_path):
    planner = ContractPlanner(SimpleNamespace(canonical_root=Path(tmp_path)))
    payload = {"items": [
        _item("T01", paths=["analysis/inventory.json"]),
        _item("FINAL", kind="final", depends_on=["T01"]),
    ]}
    with pytest.raises(ValueError, match="explicit user deliverables"):
        planner.validate(payload, original_prompt="Create pseudoC for 2.1.308.")


def test_planner_requires_pseudoc_task_path_and_final_verification(tmp_path):
    planner = ContractPlanner(SimpleNamespace(canonical_root=Path(tmp_path)))
    payload = {"items": [
        _item(
            "T01", title="Generate Ghidra pseudoc",
            prompt="Use host-governed re.lab.analyze and persist Ghidra pseudoc C files.",
            validation="Verify reimplement/pseudoc/manifest.json and C file hashes.",
            paths=["reimplement/pseudoc/manifest.json", "reimplement/pseudoc/fn_aaaaaaaa.c"],
        ),
        _item(
            "FINAL", kind="final", title="Verify pseudoc completion",
            prompt="Verify pseudoC output is complete or blocked with evidence.",
            validation="Check manifest status and all pseudoC hashes.",
            paths=["reimplement/pseudoc/manifest.json"], depends_on=["T01"],
        ),
    ]}
    result = planner.validate(payload, original_prompt="Create pseudoC for 2.1.308.")
    assert result[-1]["kind"] == "final"
