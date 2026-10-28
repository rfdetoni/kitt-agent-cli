from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from kitt.extensions.builtin_plugins.reverse_engineering.pseudocode_manifest import (
    pseudocode_completion_issue,
    validate_pseudocode_manifest,
)
from kitt.goals.contract_checks import ContractCheckRunner


def _complete_export(root: Path):
    source = root / "2.1.308"
    source_bytes = b"\x7fELF\x02\x01fixture; not executed"
    source.write_bytes(source_bytes)
    output_dir = root / "reimplement" / "pseudoc"
    output_dir.mkdir(parents=True)
    pseudoc = b"int main(void) { return 0; }\n"
    output = output_dir / "fn_aaaaaaaaaaaa_000000_00400000.c"
    output.write_bytes(pseudoc)
    manifest = {
        "schema_version": 1, "status": "COMPLETE", "complete": True,
        "source_path": "2.1.308",
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "source_size_bytes": len(source_bytes),
        "format": "ELF", "architecture": "x86:LE:64:default",
        "tool": "Ghidra Headless", "language": "x86:LE:64:default",
        "batch_count": 1,
        "batches": [{
            "start_index": 0, "next_index": 1, "truncated": False,
            "function_count": 1, "decompiled_count": 1, "failed_count": 0,
        }],
        "function_count": 1, "decompiled_count": 1, "failed_count": 0,
        "truncated": False, "next_index": 1,
        "functions": [{
            "name": "main", "entry": "00400000", "status": "DECOMPILED",
            "file": output.name, "sha256": hashlib.sha256(pseudoc).hexdigest(),
            "bytes": len(pseudoc), "error": "",
        }],
        "original_executed": False,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return source, output_dir, manifest


def test_explicit_pseudoc_request_fails_closed_without_manifest(tmp_path):
    (tmp_path / "2.1.308").write_bytes(b"\x7fELF")
    issue = pseudocode_completion_issue(tmp_path, "Create pseudoC for 2.1.308")
    assert "missing" in issue.lower()


def test_complete_manifest_checks_source_and_every_c_file(tmp_path):
    source, output_dir, manifest = _complete_export(tmp_path)
    parsed, issue = validate_pseudocode_manifest(tmp_path)
    assert issue == ""
    assert parsed["status"] == "COMPLETE"
    assert pseudocode_completion_issue(tmp_path, "Create pseudoC for 2.1.308") == ""

    output = output_dir / manifest["functions"][0]["file"]
    output.write_text("tampered", encoding="utf-8")
    assert "mismatch" in pseudocode_completion_issue(tmp_path, "Create pseudoC for 2.1.308").lower()

    output.write_bytes(b"int main(void) { return 0; }\n")
    source.write_bytes(b"\x7fELF\x02\x01tampered")
    assert "sha-256" in pseudocode_completion_issue(tmp_path, "Create pseudoC for 2.1.308").lower()


def test_final_contract_gate_cannot_accept_inventory_only_completion(monkeypatch, tmp_path):
    (tmp_path / "2.1.308").write_bytes(b"\x7fELF fixture")
    report = SimpleNamespace(
        diagnostics=[], steps=[], ok=True, status="PASS", command_output="",
        checked_paths=[], full_verification=True, workspace_verified=True,
        failure_message=lambda: "",
        as_dict=lambda: {
            "ok": True, "status": "PASS", "checked_paths": [],
            "full_verification": True, "workspace_verified": True, "steps": [],
        },
    )
    monkeypatch.setattr(
        "kitt.goals.contract_checks.VerificationOrchestrator",
        lambda *args, **kwargs: SimpleNamespace(verify=lambda *a, **kw: report),
    )
    runtime = SimpleNamespace(
        canonical_root=tmp_path,
        registry=SimpleNamespace(process_runner=object()),
        sensitive_scanner=None,
    )
    goal = SimpleNamespace(
        objective="Reverse engineer this ELF and create pseudoC in reimplement/pseudoc.",
        capabilities=[], conversation_id="test",
    )
    item = SimpleNamespace(
        kind="final", check_ids=[], paths=[], prompt="Verify all deliverables",
        validation_prompt="Confirm the pseudoc output exists.", criteria=[],
    )
    result = ContractCheckRunner(runtime).run(goal, item, [])
    assert result.evidence["ok"] is False
    assert result.evidence["status"] == "FAIL"
    assert any(not check.passed and check.name == "Ghidra pseudocode completeness"
               for check in result.checks)


def test_contract_gate_rejects_a_partial_export(tmp_path):
    _, output_dir, manifest = _complete_export(tmp_path)
    manifest["status"] = "PARTIAL"
    manifest["complete"] = False
    manifest["truncated"] = True
    manifest["batches"][0]["truncated"] = True
    (output_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert "incomplete" in pseudocode_completion_issue(
        tmp_path, "Create pseudoC for 2.1.308"
    ).lower()
