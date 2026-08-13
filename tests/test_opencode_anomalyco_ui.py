import shutil
import tempfile
import unittest
from pathlib import Path

from kitt.core.runtime import KittRuntime
from kitt.core.runtime_config import RuntimeConfig
from kitt.ui.app import KittUIApp
from kitt.ui.commands import CommandRegistry
from kitt.ui.components.command_palette import CommandPaletteComponent
from kitt.ui.components.home import HomeComponent
from kitt.ui.components.permission_card import PermissionCardComponent
from kitt.ui.components.sidebar import SidebarComponent
from kitt.ui.components.status_bar import StatusBarComponent
from kitt.ui.layout import LayoutDimensions
from kitt.ui.state import UIState
from kitt.ui.theme import DEFAULT_THEME, Theme

class TestOpenCodeAnomalyCoUI(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_responsive_layout_breakpoints(self):
        """Verify layout dimensions calculation for mobile (<80), tablet (80-119) and desktop (>=120)."""
        mobile = LayoutDimensions(width=70, height=24)
        self.assertEqual(mobile.mode, "mobile")
        self.assertEqual(mobile.sidebar_width, 0)
        self.assertEqual(mobile.transcript_width, 70)

        tablet = LayoutDimensions(width=100, height=30)
        self.assertEqual(tablet.mode, "tablet")
        self.assertEqual(tablet.sidebar_width, 0)

        desktop = LayoutDimensions(width=140, height=40)
        self.assertEqual(desktop.mode, "desktop")
        self.assertGreater(desktop.sidebar_width, 0)

    def test_02_knight_rider_theme_formatting(self):
        """Verify theme color formatting and Knight Rider scanner frame."""
        t = DEFAULT_THEME
        self.assertIn("K.I.T.T.", t.name)
        scanner = t.scanner_frame(3, width=10)
        self.assertEqual(len(scanner), 10)
        self.assertIn("█", scanner)

    def test_03_command_palette_search(self):
        """Verify CommandRegistry search functionality."""
        reg = CommandRegistry()
        doctor_cmds = reg.search("doctor")
        self.assertEqual(len(doctor_cmds), 1)
        self.assertEqual(doctor_cmds[0].id, "doctor")

        palette = CommandPaletteComponent(reg)
        rendered = palette.render("doctor")
        self.assertIn("doctor", rendered)

    def test_03b_command_palette_prefers_exact_alias(self):
        self.assertEqual(CommandRegistry().search("/conversation")[0].id, "conversation")

    def test_04_components_render_without_error(self):
        """Verify Home, StatusBar, Sidebar, and PermissionCard components render text cleanly."""
        state = UIState(workspace_name="Test Workspace", tokens_used=150, net_saved_tokens=40)
        home = HomeComponent().render(state, width=80)
        self.assertIn("K.I.T.T.", home)

        status = StatusBarComponent().render(state, width=80)
        self.assertIn("Test Workspace", status)

        sidebar = SidebarComponent().render(state, width=40)
        self.assertIn("SYSTEM METRICS", sidebar)

        perm = PermissionCardComponent().render(state, width=80)
        self.assertIn("APPROVAL REQUIRED", perm)

    def test_05_ui_app_plain_fallback(self):
        """Verify create_backend mode=plain returns PlainLineUI without requiring prompt_toolkit."""
        from kitt.ui.capabilities import create_backend
        from kitt.ui.fallback import PlainLineUI
        with KittRuntime.build(self.temp_dir) as rt:
            backend = create_backend(rt, mode="plain")
            self.assertIsInstance(backend, PlainLineUI)

if __name__ == "__main__":
    unittest.main()
