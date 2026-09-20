from __future__ import annotations

import unittest

from kitt.core.turn_processor import TurnProcessor
from kitt.core.turn_tool_loop import TurnToolLoopMixin
from kitt.core.turn_finalization import TurnFinalizationMixin
from kitt.core.turn_context import TurnContextMixin


class TurnProcessorDecompositionTests(unittest.TestCase):
    def test_tool_loop_is_owned_by_dedicated_phase_mixin(self):
        self.assertTrue(issubclass(TurnProcessor, TurnToolLoopMixin))
        self.assertNotIn("_execute_tool_loop", TurnProcessor.__dict__)
        self.assertIs(
            TurnProcessor._execute_tool_loop,
            TurnToolLoopMixin._execute_tool_loop,
        )

    def test_context_phase_is_owned_by_dedicated_mixin(self):
        self.assertTrue(issubclass(TurnProcessor, TurnContextMixin))
        for method_name in (
            "_run_semantic_filter",
            "_build_context",
            "_build_system_prompt",
        ):
            self.assertNotIn(method_name, TurnProcessor.__dict__)
            self.assertIs(
                getattr(TurnProcessor, method_name),
                getattr(TurnContextMixin, method_name),
            )

    def test_finalization_is_owned_by_dedicated_phase_mixin(self):
        self.assertTrue(issubclass(TurnProcessor, TurnFinalizationMixin))
        self.assertNotIn("_finalize_turn", TurnProcessor.__dict__)
        self.assertIs(
            TurnProcessor._finalize_turn,
            TurnFinalizationMixin._finalize_turn,
        )


if __name__ == "__main__":
    unittest.main()
