import inspect
import unittest
from pathlib import Path

from kitt.ui.app import KittUIApp
from kitt.ui.keymap import KeyMap
from kitt.ui.model_picker_state import ModelSelectionBehavior
from kitt.ui.overlay_models import ModelSetupModel
from kitt.ui.provider_catalog_state import ProviderCatalogBehavior
from kitt.ui.provider_picker_state import ProviderPatternBehavior
from kitt.ui.provider_popup_state import ProviderPopupBehavior


class TestTUIArchitectureBoundaries(unittest.TestCase):
    def test_application_class_stays_thin(self):
        lines = inspect.getsource(KittUIApp).splitlines()
        self.assertLessEqual(
            len(lines),
            500,
            "KittUIApp must remain a lifecycle/orchestration facade; extract new responsibilities.",
        )

    def test_layout_uses_at_most_five_float_surfaces(self):
        from kitt.ui import layout

        source = Path(layout.__file__).read_text(encoding="utf-8")
        self.assertLessEqual(
            source.count("Float("),
            5,
            "Add new workflows to retained shared surfaces instead of adding another Float.",
        )

    def test_ctrl_x_global_chords_are_bounded(self):
        keymap = KeyMap()
        ctrl_x = [
            sequence
            for binding in keymap.bindings.values()
            for sequence in binding.sequences
            if sequence and sequence[0] == "c-x"
        ]
        allowed = {("c-x", "a"), ("c-x", "b"), ("c-x", "n")}
        self.assertTrue(set(ctrl_x).issubset(allowed))
        self.assertLessEqual(len(ctrl_x), 3)

    def test_model_setup_uses_small_composed_behaviors(self):
        expected = {
            ModelSelectionBehavior,
            ProviderPatternBehavior,
            ProviderCatalogBehavior,
            ProviderPopupBehavior,
        }
        self.assertTrue(expected.issubset(set(ModelSetupModel.__mro__)))
        for behavior in expected:
            with self.subTest(behavior=behavior.__name__):
                self.assertLessEqual(
                    len(inspect.getsource(behavior).splitlines()),
                    200,
                    f"{behavior.__name__} is accumulating more than one responsibility.",
                )


if __name__ == "__main__":
    unittest.main()
