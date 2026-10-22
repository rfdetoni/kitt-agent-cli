from __future__ import annotations

from kitt.core.completion_guard import _exploration_signature
from kitt.core.progress_tracker import normalize_failure_diagnostic
from kitt.core.turn_tool_helpers import _tool_stall_fingerprint


def test_equivalent_simple_queries_are_repeated_but_phrases_preserve_order():
    first = {"operation": "repo.search", "arguments": {"query": "error status"}}
    second = {"operation": "repo.search", "arguments": {"query": "status ERROR"}}
    assert _exploration_signature("kitt_runtime", first) == _exploration_signature(
        "kitt_runtime", second
    )
    assert _tool_stall_fingerprint(
        "kitt_runtime", first, success=True, output="same", error=""
    ) == _tool_stall_fingerprint(
        "kitt_runtime", second, success=True, output="same", error=""
    )
    phrase_a = {"operation": "repo.search", "arguments": {"query": '"error status"'}}
    phrase_b = {"operation": "repo.search", "arguments": {"query": '"status error"'}}
    assert _exploration_signature("kitt_runtime", phrase_a) != _exploration_signature(
        "kitt_runtime", phrase_b
    )


def test_distinct_files_and_new_results_remain_distinct_progress():
    file_a = {"operation": "repo.read", "arguments": {"path": "a.py"}}
    file_b = {"operation": "repo.read", "arguments": {"path": "b.py"}}
    assert _exploration_signature("kitt_runtime", file_a) != _exploration_signature(
        "kitt_runtime", file_b
    )
    assert _tool_stall_fingerprint(
        "kitt_runtime", file_a, success=True, output="v1", error=""
    ) != _tool_stall_fingerprint(
        "kitt_runtime", file_a, success=True, output="v2", error=""
    )
    assert normalize_failure_diagnostic(
        "test failed at 2026-10-22T12:01:00Z"
    ) == normalize_failure_diagnostic(
        "test failed at 2026-10-22T12:02:01Z"
    )
