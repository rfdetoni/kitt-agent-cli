from __future__ import annotations

import unittest

from kitt.core.turn_processor import TurnProcessor
from kitt.core.turn_tool_loop import TurnToolLoopMixin
from kitt.core.turn_finalization import TurnFinalizationMixin


class TurnProcessorDecompositionTests(unittest.TestCase):
    def test_tool_loop_is_owned_by_dedicated_phase_mixin(self):
        self.assertTrue(issubclass(TurnProcessor, TurnToolLoopMixin))
        self.assertNotIn("_execute_tool_loop", TurnProcessor.__dict__)
        self.assertIs(
            TurnProcessor._execute_tool_loop,
            TurnToolLoopMixin._execute_tool_loop,
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
