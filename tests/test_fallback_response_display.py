import asyncio
import unittest
from io import StringIO
from types import SimpleNamespace

from kitt.core.turn_events import TurnCompleted
from kitt.ui.fallback import HeadlessUI, PlainLineUI


class FinalOnlyProcessor:
    def run_turn(self, cmd):
        yield TurnCompleted(response="Resposta final.")


class TestFallbackResponseDisplay(unittest.IsolatedAsyncioTestCase):
    async def test_plainlineui_prints_turn_completed_response(self):
        runtime = SimpleNamespace(
            history=SimpleNamespace(
                repo=SimpleNamespace(save_message=lambda *a, **k: None),
                get_or_create_active=lambda: {"id": "c1"},
            ),
            processor=FinalOnlyProcessor(),
            config=SimpleNamespace(history_enabled=False),
        )
        output = StringIO()
        await PlainLineUI(runtime, input_stream=StringIO("\n"), output_stream=output).run_turn("Pergunta")
        self.assertEqual(output.getvalue(), "Resposta final.\n")

    async def test_headlessui_prints_turn_completed_response(self):
        runtime = SimpleNamespace(
            history=SimpleNamespace(
                repo=SimpleNamespace(save_message=lambda *a, **k: None),
                get_or_create_active=lambda: {"id": "c1"},
            ),
            processor=FinalOnlyProcessor(),
            config=SimpleNamespace(history_enabled=False),
        )
        output = StringIO()
        code = await HeadlessUI(runtime, "Pergunta", output_stream=output).run_async()
        self.assertEqual(code, 0)
        self.assertEqual(output.getvalue(), "Resposta final.")


if __name__ == "__main__":
    unittest.main()
