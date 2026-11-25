"""Regression coverage for bounded CodeMode data processing."""
from types import SimpleNamespace

import pytest

from kitt.runtime.programmatic_flow import _bounded, _json_tokens, _transform
from kitt.runtime.program_runtime import BoundedProgramRuntime


@pytest.mark.parametrize("value", [
    ["x" * 10000, "small"],
    {"oversized": "x" * 10000, "small": True},
    "\\\"\n\t" * 10000,
    "é🙂" * 10000,
    [{"x": 1}] * 1000,
    {str(i): i for i in range(1000)},
])
def test_output_never_exceeds_budget(value):
    result, truncated = _bounded(value, 64)
    assert truncated
    assert _json_tokens(result) <= 64


@pytest.mark.parametrize("value", [[], {}, None, True, "é", [1, 2], {"x": 1}])
def test_small_values_are_unchanged(value):
    assert _bounded(value, 64) == (value, False)


def test_oversized_first_entry_is_not_returned():
    assert _bounded(["x" * 1000], 64) == ([], True)
    assert _bounded({"x": "x" * 1000}, 64) == ({}, True)


def test_projection_does_not_mutate_or_alias_selected_input():
    source = {"rows": [{"id": 1, "value": [1], "unused": "x" * 10000}]}
    projected = _transform(source, {"path": "rows", "project": ["value"]})
    projected[0]["value"].append(2)
    assert source["rows"][0]["value"] == [1]


class FakeRuntime:
    def execute(self, operation, arguments, **kwargs):
        assert operation == "repo.list"
        return SimpleNamespace(
            requires_approval=False, success=True, data={"files": [1, 2, 3]},
            tokens_saved=0,
        )


def run(program, **kwargs):
    return BoundedProgramRuntime(FakeRuntime()).execute(
        {"program": program, **kwargs},
        turn_id="test", origin="test", capabilities={"repo.read"},
        security_context=None,
    )


def test_program_projects_results_before_retaining():
    result = run([
        {"call": "repo.list", "save": "count",
         "transform": {"path": "files", "aggregate": "count"}},
        {"return": "$count"},
    ], capture_state=True)
    assert result.success
    assert result.data["result"] == 3
    assert result.data["state"] == {"count": 3}


def test_zero_limit_does_not_execute_and_restores_shadowed_variable():
    result = run([
        {"set": "item", "value": "outer"},
        {"for_each": [1], "as": "item", "limit": 0,
         "do": [{"call": "repo.list"}]},
        {"for_each": [2], "as": "item", "do": [
            {"for_each": [3], "as": "item", "do": []},
            {"set": "observed", "value": "$item"},
        ]},
        {"return": {"item": "$item", "observed": "$observed"}},
    ])
    assert result.success
    assert result.data["tool_calls"] == 0
    assert result.data["result"] == {"item": "outer", "observed": 2}


def test_program_scope_budget_rejects_oversized_retained_values():
    result = run([
        {"set": "large", "value": "x" * (2 * 1024 * 1024)},
        {"return": True},
    ])
    assert not result.success
    assert "scope exceeds" in result.error


def test_program_collect_does_not_mutate_an_aliased_list():
    result = run([
        {"set": "bucket", "value": []},
        {"set": "alias", "value": "$bucket"},
        {"call": "repo.list", "collect": "bucket",
         "transform": {"path": "files", "aggregate": "count"}},
        {"return": {"bucket": "$bucket", "alias": "$alias"}},
    ])
    assert result.success
    assert result.data["result"] == {"bucket": [3], "alias": []}


def test_program_rejects_self_referential_save_collect():
    result = run([
        {"call": "repo.list", "save": "files", "collect": "files",
         "transform": {"path": "files"}},
    ])
    assert not result.success
    assert "distinct variables" in result.error
