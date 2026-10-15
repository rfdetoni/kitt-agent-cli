"""Examples in model instructions use the active JSON result contract."""
import json
from kitt.goals.contract import ContractPlanner
from kitt.goals.contract_validation import _VALIDATION_JSON_EXAMPLE, parse_validation_report
from kitt_protocol import decode_json_object


def test_planner_example_decodes_to_required_task_and_final():
    text = ContractPlanner._planning_prompt("Inspect a small project")
    example = text.split("Example:\n", 1)[1].split("\n",1)[0]
    payload = decode_json_object(example)
    assert [item["kind"] for item in payload["items"]] == ["task", "final"]
    assert payload["items"][1]["depends_on"] == ["T01"]
    for item in payload["items"]:
        assert all(name in item for name in (
            "local_id", "kind", "title", "prompt", "validation_prompt",
            "success_criteria", "check_ids", "paths", "depends_on"
        ))
    assert "K2 " not in text and "KAP" not in text


def test_review_prompt_and_validation_example_use_json():
    prompt = ContractPlanner._review_prompt("Improve something", [], "LOW")
    assert json.dumps({"verdict":"OK","issues":[]},separators=(",",":")) in prompt
    assert "K2 " not in prompt and "KAP" not in prompt
    payload = decode_json_object(_VALIDATION_JSON_EXAMPLE)
    assert payload["verdict"] == "FAIL"
    assert payload["evidence"] == ["specific current workspace evidence"]
    assert set(payload["issues"][0]) == {"severity","location","problem","fix"}
    assert parse_validation_report(_VALIDATION_JSON_EXAMPLE).verdict == "FAIL"
