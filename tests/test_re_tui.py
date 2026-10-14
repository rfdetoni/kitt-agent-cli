"""TUI RE manager smoke regressions for command discovery and wizard routing."""
from __future__ import annotations

from types import SimpleNamespace

from kitt.ui.commands import CommandRegistry
from kitt.ui import re_panel


class _Interactions:
    def begin(self, *_): pass
    def add_row(self, *_): pass
    def add_text(self, *_): pass


class _Buffer:
    def __init__(self):
        self.reset_count = 0

    def reset(self):
        self.reset_count += 1


def test_re_manager_is_in_command_palette():
    registry = CommandRegistry()
    assert registry.find("/re").id == "re"
    assert registry.find("/re-lab").id == "re"


def test_re_manager_renders_missing_guests_without_starting_anything():
    ui = SimpleNamespace(
        re_snapshot={"guests": {"windows": {"status": "missing"},
                                "linux": {"status": "missing"}},
                     "rea": {"installed": False}},
        re_selected="windows",
        re_error="",
        interactions=_Interactions(),
    )
    text = re_panel.panel_text(ui)
    assert "WINDOWS" in text
    assert "LINUX" in text
    assert "[p] Preparar / Helper" in text


def test_re_setup_opens_source_wizard_without_booting_a_vm():
    actions = []
    buf = _Buffer()
    ui = SimpleNamespace(
        re_selected="windows",
        re_setup_source_buffer=buf,
        re_setup_source_control=object(),
        open_overlay=lambda *args, **kwargs: actions.append((args, kwargs)),
    )
    re_panel.open_setup(ui)
    assert buf.reset_count == 1
    assert actions[0][0][0] == "re_setup"
    assert actions[0][1]["parent_name"] == "re_panel"
