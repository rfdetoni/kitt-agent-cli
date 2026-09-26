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


def test_transcript_virtualizes_old_blocks_until_expanded():
    blocks = [TranscriptBlock(f"block-{i}", "assistant", f"message {i}") for i in range(500)]
    ui = _ui(blocks)
    ui.state.transcript_window_blocks = 120
    ui.state.transcript_window_step = 120

    rendered = "".join(text for _style, text in _transcript_text(ui))
    assert "380 blocos anteriores virtualizados" in rendered
    assert "message 0" not in rendered
    assert "message 499" in rendered

    ui.state.transcript_window_blocks = 500
    rendered_full = "".join(text for _style, text in _transcript_text(ui))
    assert "virtualizados" not in rendered_full
    assert "message 0" in rendered_full
