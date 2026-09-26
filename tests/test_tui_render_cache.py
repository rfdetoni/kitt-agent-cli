from types import SimpleNamespace

from kitt.ui.render.core import _transcript_text
from kitt.ui.state import TranscriptBlock


def _ui(blocks):
    state = SimpleNamespace(
        transcript=blocks,
        active_turn_id=None,
        is_thinking=False,
        is_executing_tool=False,
        unseen_output=False,
    )
    return SimpleNamespace(state=state)


def test_transcript_render_cache_reuses_stable_fragments():
    block = TranscriptBlock("block-1", "assistant", "hello")
    ui = _ui([block])

    first = _transcript_text(ui)
    cached = ui._transcript_render_cache["block-1"]
    second = _transcript_text(ui)

    assert first == second
    assert ui._transcript_render_cache["block-1"] is cached


def test_transcript_render_does_not_mutate_running_status():
    block = TranscriptBlock("block-1", "tool", "running", status="running")
    ui = _ui([block])

    _transcript_text(ui)

    assert block.status == "running"
