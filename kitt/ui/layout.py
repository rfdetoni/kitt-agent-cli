from __future__ import annotations

from dataclasses import dataclass

from kitt import KITT_VERSION
from kitt.ui.scroll import make_index_wheel_handler, register_scrollable_window


@dataclass(frozen=True)
class LayoutDimensions:
    width: int
    height: int

    @property
    def mode(self) -> str:
        return "desktop" if self.width >= 120 else "tablet" if self.width >= 80 else "mobile"

    @property
    def sidebar_width(self) -> int:
        return min(42, max(38, self.width // 3)) if self.mode == "desktop" else 0

    @property
    def transcript_width(self) -> int:
        return self.width - self.sidebar_width


def _version_text() -> str:
    return f" KITT Agent CLI v{KITT_VERSION} "


_MODEL_STEPS = {
    "model_setup": "Modelos",
    "provider_popup": "Modelos › Provedores",
    "add_provider": "Modelos › Provedores › Adicionar",
    "provider_endpoint": "Modelos › Endpoint",
    "auth_login": "Modelos › Autenticação",
}

_CONTEXT_TABS = {
    "session_picker": "Conversas",
    "timeline": "Timeline",
    "diff": "Diff",
    "agents": "Agentes",
    "autonomy_control": "Autonomia",
    "help": "Ajuda",
}


def build_root_container(ui):
    """Build a retained container tree with five logical overlay surfaces."""
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.layout import (
        ConditionalContainer,
        DynamicContainer,
        Float,
        FloatContainer,
        HSplit,
        VSplit,
        Window,
    )
    from prompt_toolkit.layout.containers import WindowAlign
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.dimension import Dimension
    from prompt_toolkit.layout.margins import ScrollbarMargin
    from prompt_toolkit.layout.menus import CompletionsMenu
    from prompt_toolkit.widgets import Box, Frame

    visible = lambda name: Condition(lambda: ui.state.active_overlay == name)
    desktop_sidebar = Condition(lambda: ui.state.route == "session" and ui.dimensions.mode == "desktop")
    tablet_sidebar = Condition(
        lambda: ui.state.route == "session" and ui.dimensions.mode == "tablet" and ui.state.sidebar_open
    )
    short = Condition(lambda: ui.state.height < 18)
    model_wizard_visible = Condition(lambda: ui.state.active_overlay in _MODEL_STEPS)
    context_panel_visible = Condition(lambda: ui.state.active_overlay in _CONTEXT_TABS)

    def status_bar():
        version_text = _version_text()
        return VSplit([
            Window(ui.status_control, height=1, wrap_lines=False, style="class:status"),
            Window(
                FormattedTextControl(version_text),
                width=len(version_text),
                height=1,
                wrap_lines=False,
                align=WindowAlign.RIGHT,
                style="class:status",
            ),
        ])

    def after_transcript_scroll(event_type, window):
        from prompt_toolkit.mouse_events import MouseEventType
        if event_type == MouseEventType.SCROLL_UP:
            ui.state.follow_tail = False
        elif event_type == MouseEventType.SCROLL_DOWN:
            info = getattr(window, "render_info", None)
            if info is not None and info.bottom_visible:
                ui.state.follow_tail = True
                ui.state.unseen_output = False

    transcript = register_scrollable_window(
        ui,
        "transcript",
        Window(
            ui.transcript_control,
            wrap_lines=True,
            right_margins=[ScrollbarMargin(display_arrows=True)],
            style="class:surface",
            allow_scroll_beyond_bottom=False,
        ),
        after_scroll=after_transcript_scroll,
    )
    ui.transcript_window = transcript

    sidebar = register_scrollable_window(
        ui,
        "sidebar",
        Window(
            ui.sidebar_control,
            width=Dimension(min=38, max=42),
            wrap_lines=False,
            right_margins=[ScrollbarMargin(display_arrows=True)],
            style="class:surface.raised",
        ),
    )
    ui.sidebar_window = sidebar
    body = VSplit([transcript, ConditionalContainer(sidebar, filter=desktop_sidebar)], padding=1)

    header = ConditionalContainer(
        Window(ui.header_control, height=1, wrap_lines=False, style="class:surface.raised"),
        filter=~short,
    )
    live_agents = ConditionalContainer(
        Window(ui.live_agents_control, height=1, wrap_lines=False, style="class:primary"),
        filter=Condition(lambda: bool(ui.state.active_tasks)),
    )
    prompt_window = register_scrollable_window(
        ui,
        "prompt",
        Window(ui.prompt_control, height=Dimension(min=3, max=8), wrap_lines=True),
    )
    ui.prompt_window = prompt_window
    prompt = Frame(
        prompt_window,
        title=lambda: (
            f"Prompt [{ui.state.turn_mode.upper()}]  │  F4: Alternar Modo  │  "
            "F12: Modelos  │  Alt+Enter: Nova Linha"
        ),
    )
    session = HSplit([header, body, live_agents, prompt, status_bar()])

    home = HSplit([
        Window(height=Dimension(weight=1)),
        Window(ui.home_control, height=13, align=WindowAlign.CENTER),
        Box(
            Frame(
                prompt_window,
                title=lambda: f"K.I.T.T. [{ui.state.turn_mode.upper()}]  │  F4: Modo  │  F12: Modelos",
            ),
            padding_left=4,
            padding_right=4,
        ),
        Window(ui.hints_control, height=2, align=WindowAlign.CENTER),
        Window(height=Dimension(weight=1)),
        status_bar(),
    ])
    content = DynamicContainer(lambda: home if ui.state.route == "home" else session)

    permission = register_scrollable_window(ui, "permission", ui.permission_window)

    palette_window = register_scrollable_window(
        ui,
        "palette",
        Window(ui.palette_control, wrap_lines=False, right_margins=[ScrollbarMargin()]),
    )
    palette = HSplit([
        Window(ui.palette_search_control, height=1),
        palette_window,
    ])

    ui.sidebar_mobile_control = FormattedTextControl(ui._sidebar_text)
    sidebar_mobile = register_scrollable_window(
        ui,
        "sidebar_mobile",
        Window(
            ui.sidebar_mobile_control,
            wrap_lines=False,
            right_margins=[ScrollbarMargin(display_arrows=True)],
        ),
    )

    model_setup_window = register_scrollable_window(
        ui,
        "model_setup",
        Window(ui.model_setup_control, wrap_lines=False, right_margins=[ScrollbarMargin()]),
        wheel_handler=make_index_wheel_handler(
            ui.model_setup_model.move_model,
            invalidate=lambda: ui.application.invalidate() if ui.application else None,
        ),
    )
    provider_popup_window = register_scrollable_window(
        ui,
        "provider_popup",
        Window(ui.provider_popup_control, wrap_lines=False, right_margins=[ScrollbarMargin()]),
        wheel_handler=make_index_wheel_handler(
            ui.model_setup_model.move_popup_selection,
            invalidate=lambda: ui.application.invalidate() if ui.application else None,
        ),
    )

    model_steps = {
        "model_setup": HSplit([
            Window(ui.model_setup_header_control, height=5),
            VSplit([
                Window(FormattedTextControl(" 🔍 Filtrar Modelo: "), width=19),
                Window(ui.model_setup_search_control, height=1),
            ]),
            Window(
                FormattedTextControl(
                    " ────────────────────────────────────────────────────────────────────────────"
                ),
                height=1,
            ),
            model_setup_window,
        ]),
        "provider_popup": provider_popup_window,
        "add_provider": HSplit([
            Window(ui.add_provider_help_control, height=3),
            VSplit([
                Window(FormattedTextControl(" Nome do Provedor: "), width=20),
                Window(ui.add_provider_name_control, height=1),
            ]),
            VSplit([
                Window(FormattedTextControl(" Base URL:         "), width=20),
                Window(ui.add_provider_url_control, height=1),
            ]),
        ]),
        "provider_endpoint": HSplit([
            Window(ui.provider_endpoint_help_control, height=3),
            VSplit([
                Window(FormattedTextControl(" Endpoint URL: "), width=16),
                Window(ui.provider_endpoint_control, height=1),
            ]),
        ]),
        "auth_login": HSplit([
            Window(ui.auth_login_help_control, height=3),
            VSplit([
                Window(FormattedTextControl(" API Key / Token: "), width=18),
                Window(ui.auth_login_control, height=1),
            ]),
        ]),
    }
    model_wizard = DynamicContainer(
        lambda: model_steps.get(ui.state.active_overlay, model_steps["model_setup"])
    )

    context_windows = {
        "session_picker": register_scrollable_window(
            ui,
            "session_picker",
            Window(ui.session_picker_control, wrap_lines=False, right_margins=[ScrollbarMargin()]),
        ),
        "timeline": register_scrollable_window(
            ui,
            "timeline",
            Window(ui.timeline_control, wrap_lines=False, right_margins=[ScrollbarMargin()]),
        ),
        "diff": register_scrollable_window(
            ui,
            "diff",
            Window(ui.diff_control, wrap_lines=False, right_margins=[ScrollbarMargin(display_arrows=True)]),
        ),
        "agents": register_scrollable_window(
            ui,
            "agents",
            Window(ui.agents_control, wrap_lines=False, right_margins=[ScrollbarMargin()]),
        ),
        "autonomy_control": register_scrollable_window(
            ui,
            "autonomy",
            Window(ui.autonomy_control, wrap_lines=False, right_margins=[ScrollbarMargin()]),
        ),
        "help": register_scrollable_window(
            ui,
            "help",
            Window(ui.help_control, wrap_lines=False, right_margins=[ScrollbarMargin()]),
        ),
    }
    context_panel = DynamicContainer(
        lambda: context_windows.get(ui.state.active_overlay, context_windows["help"])
    )

    floats = [
        Float(
            content=ConditionalContainer(
                Box(Frame(permission, title="Approval"), padding=1),
                filter=visible("permission"),
            ),
            left=4,
            right=4,
            top=1,
            bottom=2,
        ),
        Float(
            xcursor=True,
            ycursor=True,
            attach_to_window=ui.prompt_window,
            content=CompletionsMenu(max_height=12, scroll_offset=1),
        ),
        Float(
            content=ConditionalContainer(
                Box(Frame(palette, title="Command Palette"), padding=1),
                filter=visible("palette"),
            ),
            left=8,
            right=8,
            top=2,
            bottom=3,
        ),
        Float(
            content=ConditionalContainer(
                Box(Frame(sidebar_mobile, title="Sidebar"), padding=1),
                filter=tablet_sidebar,
            ),
            right=1,
            top=2,
            bottom=2,
            width=42,
        ),
        Float(
            content=ConditionalContainer(
                Box(
                    Frame(
                        model_wizard,
                        title=lambda: _MODEL_STEPS.get(ui.state.active_overlay, "Modelos"),
                    ),
                    padding=1,
                ),
                filter=model_wizard_visible,
            ),
            left=6,
            right=6,
            top=1,
            bottom=2,
        ),
        Float(
            content=ConditionalContainer(
                Box(
                    Frame(
                        context_panel,
                        title=lambda: (
                            f"Contexto › {_CONTEXT_TABS.get(ui.state.active_overlay, 'Ajuda')} "
                            "│ ←/→ alternar"
                        ),
                    ),
                    padding=1,
                ),
                filter=context_panel_visible,
            ),
            left=6,
            right=6,
            top=2,
            bottom=3,
        ),
        Float(
            content=ConditionalContainer(
                Frame(Window(ui.toast_control, height=3, wrap_lines=False), title="Notice"),
                filter=Condition(lambda: bool(ui.state.active_toasts())),
            ),
            right=1,
            top=1,
            width=60,
        ),
    ]
    return FloatContainer(content=content, floats=floats)
