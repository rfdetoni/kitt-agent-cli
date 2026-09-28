from __future__ import annotations

from dataclasses import dataclass

from kitt.compaction.service import CompactionService
from kitt.context.tool_receipts import (
    compact_consumed_tool_results,
    externalize_large_tool_results,
)


@dataclass
class _Artifact:
    id: str = "art_test"


class _Store:
    def __init__(self):
        self.content = None

    def put(self, workspace_id, content, artifact_type, summary, **kwargs):
        self.content = content
        return _Artifact()


def test_large_tool_output_is_externalized_before_receipt_compaction():
    store = _Store()
    messages = [
        {"role": "user", "content": "repo.read result from the host.\n" + ("payload " * 500)}
    ]
    assert externalize_large_tool_results(
        messages,
        store=store,
        workspace_id="ws",
        conversation_id="conv",
        turn_id="turn",
        min_tokens=10,
    ) == 1
    assert "Artifact ID art_test" in messages[0]["content"]
    assert compact_consumed_tool_results(messages, min_tokens=10) == 1
    assert "artifact_id=art_test" in messages[0]["content"]
    assert store.content is not None


def test_structured_working_state_preserves_execution_signals():
    state = CompactionService._working_state(
        "Goal: migrate API\nDecision: use typed IR\nERROR parser failed\n"
        "TODO rerun tests\nsrc/api/service.py changed\npytest passed",
        "Migration in progress",
        ["Must keep compatibility"],
    )
    assert "Must keep compatibility" in state.constraints_and_decisions
    assert any("ERROR" in item for item in state.errors_and_corrections)
    assert any("pytest" in item for item in state.validation_state)
    assert "src/api/service.py" in state.affected_artifacts
