import unittest
from io import StringIO
from types import SimpleNamespace

from kitt.core.turn_events import TextDelta, TurnCompleted, TurnStarted
from kitt.ui.fallback import HeadlessUI
from kitt.ui.reducer import reduce_ui_event
from kitt.ui.state import UIState


class TestResponseFinalization(unittest.IsolatedAsyncioTestCase):
    def test_tui_appends_final_response_when_previous_answer_exists(self):
        state = UIState()
        state.append_message("assistant", "Resposta anterior.")

        reduce_ui_event(state, TurnStarted(turn_id="t1", conversation_id="c1", prompt="Pergunta"))
        reduce_ui_event(state, TurnCompleted(response="Resposta final."))

        assistant_texts = [block.text for block in state.transcript if block.kind == "assistant"]
        self.assertEqual(assistant_texts, ["Resposta anterior.", "Resposta final."])

    def test_tui_replaces_partial_stream_with_final_response(self):
        state = UIState()

        reduce_ui_event(state, TurnStarted(turn_id="t1", conversation_id="c1", prompt="Pergunta"))
        reduce_ui_event(state, TextDelta(delta="Resposta par"))
        reduce_ui_event(state, TurnCompleted(response="Resposta parcial completa."))

        assistant_texts = [block.text for block in state.transcript if block.kind == "assistant"]
        self.assertEqual(assistant_texts, ["Resposta parcial completa."])

    async def test_headless_prints_only_missing_suffix_when_final_extends_stream(self):
        class Processor:
            def run_turn(self, cmd):
                yield TextDelta(delta="Resposta par")
                yield TurnCompleted(response="Resposta parcial completa.")

        runtime = SimpleNamespace(
            history=SimpleNamespace(
                repo=SimpleNamespace(save_message=lambda *a, **k: None),
                get_or_create_active=lambda: {"id": "c1"},
            ),
            processor=Processor(),
            config=SimpleNamespace(history_enabled=False),
        )
        output = StringIO()

        code = await HeadlessUI(runtime, "Pergunta", output_stream=output).run_async()

        self.assertEqual(code, 0)
        self.assertEqual(output.getvalue(), "Resposta parcial completa.")


if __name__ == "__main__":
    unittest.main()
