"""Unit tests for deterministic priority-driven OverlayManager."""
import unittest
from unittest.mock import MagicMock

from kitt.ui.overlay_manager import (
    OVERLAY_SPECS,
    OverlayFrame,
    OverlayManager,
    OverlayPriority,
)
from kitt.ui.state import UIState


class TestOverlayPriorityManager(unittest.TestCase):

    def setUp(self):
        self.mock_app = MagicMock()
        self.mock_app.state = UIState()
        self.mock_app.focus_stack = []
        self.mock_app.application = MagicMock()
        self.mock_app.application.layout.current_control = "prompt_ctrl"
        self.manager = OverlayManager(self.mock_app)

    def test_priority_hierarchy_ordering(self):
        self.assertGreater(
            OVERLAY_SPECS["permission"].priority,
            OVERLAY_SPECS["auth_login"].priority,
        )
        self.assertGreater(
            OVERLAY_SPECS["auth_login"].priority,
            OVERLAY_SPECS["model_setup"].priority,
        )
        self.assertGreater(
            OVERLAY_SPECS["model_setup"].priority,
            OVERLAY_SPECS["provider_popup"].priority,
        )
        self.assertGreater(
            OVERLAY_SPECS["provider_popup"].priority,
            OVERLAY_SPECS["palette"].priority,
        )
        self.assertGreater(
            OVERLAY_SPECS["palette"].priority,
            OVERLAY_SPECS["help"].priority,
        )

    def test_open_overlay_pushes_frame_and_syncs_state(self):
        self.manager.open("model_setup", control="model_setup_ctrl")
        self.assertEqual(self.mock_app.state.active_overlay, "model_setup")
        self.assertEqual(self.mock_app.state.overlay_stack, ["model_setup"])
        self.assertEqual(len(self.manager.frames), 1)
        self.assertEqual(self.manager.top_frame().preferred_focus, "model_setup_ctrl")

    def test_higher_priority_suspends_lower_priority_parent(self):
        # 1. Open model_setup
        self.manager.open("model_setup", control="model_setup_ctrl")
        # 2. Open auth_login (higher priority)
        self.manager.open("auth_login", control="auth_ctrl")
        self.assertEqual(self.mock_app.state.active_overlay, "auth_login")
        self.assertTrue(self.manager.frames[0].suspended)
        self.assertFalse(self.manager.frames[1].suspended)

        # 3. Open permission (security priority)
        self.manager.open("permission", control="permission_ctrl")
        self.assertEqual(self.mock_app.state.active_overlay, "permission")
        self.assertTrue(self.manager.frames[1].suspended)

        # Close permission -> restores auth_login
        closed = self.manager.close()
        self.assertEqual(closed, "permission")
        self.assertEqual(self.mock_app.state.active_overlay, "auth_login")
        self.assertFalse(self.manager.frames[1].suspended)

        # Close auth_login -> restores model_setup
        closed = self.manager.close()
        self.assertEqual(closed, "auth_login")
        self.assertEqual(self.mock_app.state.active_overlay, "model_setup")
        self.assertFalse(self.manager.frames[0].suspended)

        # Close model_setup -> returns to base
        closed = self.manager.close()
        self.assertEqual(closed, "model_setup")
        self.assertIsNone(self.mock_app.state.active_overlay)

    def test_duplicate_suppression_prevents_duplicate_modals(self):
        self.manager.open("palette", control="palette_ctrl")
        self.manager.open("palette", control="palette_ctrl")
        self.assertEqual(len(self.manager.frames), 1)

    def test_focus_restoration_to_previous_control(self):
        self.mock_app.application.layout.current_control = "search_box"
        self.manager.open("help", control="help_ctrl")
        self.manager.close()
        self.mock_app.application.layout.focus.assert_called_with("search_box")


if __name__ == "__main__":
    unittest.main()
