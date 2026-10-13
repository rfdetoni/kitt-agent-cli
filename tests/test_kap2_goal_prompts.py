"""Generated KAP/2 planning/review examples must be accepted by the current protocol."""
from kitt.goals.contract import ContractPlanner
from kitt_protocol import decode_kap2_content


def test_planner_example_decodes_to_required_task_and_final():
    text = ContractPlanner._planning_prompt("Inspect a small project")
    assert "KAP/1" not in text
    example = text.split("Example:\n", 1)[1].split("K2/END", 1)[0] + "K2/END"
    payload = decode_kap2_content(example)
    assert [item["kind"] for item in payload["items"]] == ["task", "final"]
    assert payload["items"][1]["depends_on"] == ["T01"]
    for item in payload["items"]:
        assert all(name in item for name in (
            "local_id", "kind", "title", "prompt", "validation_prompt",
            "success_criteria", "check_ids", "paths", "depends_on"
        ))


def test_contract_review_example_decodes_without_legacy_envelope():
    prompt = ContractPlanner._review_prompt("Improve something", [], "LOW")
    assert "KAP/1" not in prompt
    example = prompt.split("e.g.:\n", 1)[1].split("K2/END", 1)[0] + "K2/END"
    payload = decode_kap2_content(example)
    assert payload == {"verdict": "OK", "issues": []}


def test_independent_validation_example_decodes_and_preserves_issue_shape():
    from kitt.goals.contract_validation import (
        _VALIDATION_KAP2_EXAMPLE, parse_validation_report
    )
    payload = decode_kap2_content(_VALIDATION_KAP2_EXAMPLE)
    assert payload["verdict"] == "FAIL"
    assert payload["evidence"] == ["specific current workspace evidence"]
    assert set(payload["issues"][0]) == {"severity", "location", "problem", "fix"}
    report = parse_validation_report(_VALIDATION_KAP2_EXAMPLE)
    assert report.verdict == "FAIL"
    assert len(report.issues) == 1
