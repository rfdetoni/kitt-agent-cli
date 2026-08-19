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

    def test_parent_child_submodal_navigation(self):
        # 1. model_setup -> provider_popup -> add_provider
        self.manager.open("model_setup", control="ctrl_model")
        self.assertEqual(self.mock_app.state.active_overlay, "model_setup")

        # Open authorized child provider_popup
        self.manager.open("provider_popup", control="ctrl_popup", parent_name="model_setup")
        self.assertEqual(self.mock_app.state.active_overlay, "provider_popup")
        self.assertTrue(self.manager.frames[0].suspended)

        # Open authorized child add_provider
        self.manager.open("add_provider", control="ctrl_add", parent_name="provider_popup")
        self.assertEqual(self.mock_app.state.active_overlay, "add_provider")
        self.assertTrue(self.manager.frames[1].suspended)

        # Close add_provider -> back to provider_popup
        self.assertEqual(self.manager.close(), "add_provider")
        self.assertEqual(self.mock_app.state.active_overlay, "provider_popup")
        self.assertFalse(self.manager.frames[1].suspended)

        # Close provider_popup -> back to model_setup
        self.assertEqual(self.manager.close(), "provider_popup")
        self.assertEqual(self.mock_app.state.active_overlay, "model_setup")
        self.assertFalse(self.manager.frames[0].suspended)

        # Close model_setup -> back to base
        self.assertEqual(self.manager.close(), "model_setup")
        self.assertIsNone(self.mock_app.state.active_overlay)

    def test_security_preemption_over_submodal(self):
        # model_setup -> provider_popup -> permission preempts both
        self.manager.open("model_setup", control="ctrl_model")
        self.manager.open("provider_popup", control="ctrl_popup", parent_name="model_setup")
        self.assertEqual(self.mock_app.state.active_overlay, "provider_popup")

        # Security permission arrives
        self.manager.open("permission", control="ctrl_perm")
        self.assertEqual(self.mock_app.state.active_overlay, "permission")

        # Closing permission restores provider_popup
        self.assertEqual(self.manager.close(), "permission")
        self.assertEqual(self.mock_app.state.active_overlay, "provider_popup")

    def test_lower_priority_cannot_open_over_security(self):
        self.manager.open("permission", control="ctrl_perm")
        self.assertEqual(self.mock_app.state.active_overlay, "permission")

        # Attempt to open help or palette (lower priority, non-child)
        self.manager.open("help", control="ctrl_help")
        # Should remain on permission
        self.assertEqual(self.mock_app.state.active_overlay, "permission")
        self.assertEqual(len(self.manager.frames), 1)

    def test_palette_to_help_and_back(self):
        self.manager.open("palette", control="ctrl_palette")
        self.assertEqual(self.mock_app.state.active_overlay, "palette")

        self.manager.open("help", control="ctrl_help", parent_name="palette")
        self.assertEqual(self.mock_app.state.active_overlay, "help")

        self.assertEqual(self.manager.close(), "help")
        self.assertEqual(self.mock_app.state.active_overlay, "palette")


if __name__ == "__main__":
    unittest.main()
