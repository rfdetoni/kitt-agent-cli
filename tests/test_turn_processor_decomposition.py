from __future__ import annotations

import unittest

from kitt.core.turn_processor import TurnProcessor
from kitt.core.turn_tool_loop import TurnToolLoopMixin


class TurnProcessorDecompositionTests(unittest.TestCase):
    def test_tool_loop_is_owned_by_dedicated_phase_mixin(self):
        self.assertTrue(issubclass(TurnProcessor, TurnToolLoopMixin))
        self.assertNotIn("_execute_tool_loop", TurnProcessor.__dict__)
        self.assertIs(
            TurnProcessor._execute_tool_loop,
            TurnToolLoopMixin._execute_tool_loop,
        )


if __name__ == "__main__":
    unittest.main()
