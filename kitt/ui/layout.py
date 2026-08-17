from __future__ import annotations

from dataclasses import dataclass


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


def build_root_container(ui):
    """Build retained prompt_toolkit container tree around live UI state."""
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.layout import ConditionalContainer, DynamicContainer, Float, FloatContainer, HSplit, VSplit, Window
    from prompt_toolkit.layout.containers import WindowAlign
    from prompt_toolkit.layout.dimension import Dimension
    from prompt_toolkit.layout.margins import ScrollbarMargin
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.widgets import Box, Frame
    from prompt_toolkit.layout.menus import CompletionsMenu

    visible = lambda name: Condition(lambda: ui.state.active_overlay == name)
    desktop_sidebar = Condition(lambda: ui.state.route == "session" and ui.dimensions.mode == "desktop")
    tablet_sidebar = Condition(lambda: ui.state.route == "session" and ui.dimensions.mode == "tablet" and ui.state.sidebar_open)
    short = Condition(lambda: ui.state.height < 18)

    transcript = Window(
        ui.transcript_control, wrap_lines=True, right_margins=[ScrollbarMargin(display_arrows=True)],
        style="class:surface", allow_scroll_beyond_bottom=False,
    )
    ui.transcript_window = transcript
    sidebar = Window(ui.sidebar_control, width=Dimension(min=38, max=42), wrap_lines=False, right_margins=[ScrollbarMargin(display_arrows=True)], style="class:surface.raised")
    body = VSplit([transcript, ConditionalContainer(sidebar, filter=desktop_sidebar)], padding=1)
    header = ConditionalContainer(Window(ui.header_control, height=1, wrap_lines=False, style="class:surface.raised"), filter=~short)
    approval_inline = ConditionalContainer(Frame(Window(ui.permission_control, height=Dimension(min=4, max=10), wrap_lines=False, right_margins=[ScrollbarMargin(display_arrows=True)]), title="Approval"), filter=visible("permission"))
    live_agents = ConditionalContainer(Window(ui.live_agents_control, height=1, wrap_lines=False, style="class:primary"), filter=Condition(lambda: bool(ui.state.active_tasks)))
    prompt_window = Window(ui.prompt_control, height=Dimension(min=3, max=8), wrap_lines=True)
    ui.prompt_window = prompt_window
    prompt = Frame(prompt_window, title="Prompt  Alt+Enter newline")
    session = HSplit([header, body, approval_inline, live_agents, prompt, Window(ui.status_control, height=1, wrap_lines=False, style="class:status")])

    home = HSplit([
        Window(height=Dimension(weight=1)),
        Window(ui.home_control, height=13, align=WindowAlign.CENTER),
        Box(Frame(prompt_window, title="Ask K.I.T.T."), padding_left=4, padding_right=4),
        Window(ui.hints_control, height=2, align=WindowAlign.CENTER),
        Window(height=Dimension(weight=1)),
        Window(ui.status_control, height=1, wrap_lines=False, style="class:status"),
    ])
    content = DynamicContainer(lambda: home if ui.state.route == "home" else session)

    def overlay(control, title, width=72, height=16):
        return ConditionalContainer(
            Box(Frame(Window(control, wrap_lines=False, right_margins=[ScrollbarMargin(display_arrows=True)]), title=title), padding=1),
            filter=visible(title.lower().replace(" ", "_")),
        )

    floats = [
        Float(xcursor=True, ycursor=True, attach_to_window=ui.prompt_window, content=CompletionsMenu(max_height=12, scroll_offset=1)),
        Float(content=ConditionalContainer(Box(Frame(HSplit([
            Window(ui.palette_search_control, height=1), Window(ui.palette_control, wrap_lines=False, right_margins=[ScrollbarMargin()])
        ]), title="Command Palette"), padding=1), filter=visible("palette")), left=8, right=8, top=2, bottom=3),
        Float(content=ConditionalContainer(Box(Frame(Window(ui.sidebar_control, wrap_lines=False, right_margins=[ScrollbarMargin(display_arrows=True)]), title="Sidebar"), padding=1), filter=tablet_sidebar), right=1, top=2, bottom=2, width=42),
        Float(content=ConditionalContainer(Box(Frame(Window(ui.session_picker_control, wrap_lines=False, right_margins=[ScrollbarMargin()]), title="Session Picker"), padding=1), filter=visible("session_picker")), left=8, right=8, top=2, bottom=3),
        Float(content=ConditionalContainer(Box(Frame(Window(ui.timeline_control, wrap_lines=False, right_margins=[ScrollbarMargin()]), title="Timeline"), padding=1), filter=visible("timeline")), left=8, right=8, top=2, bottom=3),
        Float(content=ConditionalContainer(Box(Frame(Window(ui.diff_control, wrap_lines=False, right_margins=[ScrollbarMargin(display_arrows=True)]), title="Diff Viewer"), padding=1), filter=visible("diff")), left=4, right=4, top=1, bottom=2),
        Float(content=ConditionalContainer(Box(Frame(Window(ui.model_setup_control, wrap_lines=False, right_margins=[ScrollbarMargin()]), title="Model Configuration"), padding=1), filter=visible("model_setup")), left=8, right=8, top=2, bottom=3),
        Float(content=ConditionalContainer(Box(Frame(Window(ui.provider_popup_control, wrap_lines=False, right_margins=[ScrollbarMargin()]), title="Selecione o Provedor (★ Favoritos)"), padding=1), filter=visible("provider_popup")), left=8, right=8, top=2, bottom=3),
        Float(content=ConditionalContainer(Box(Frame(HSplit([
            Window(ui.add_provider_help_control, height=3),
            VSplit([Window(FormattedTextControl(" Nome do Provedor: "), width=20), Window(ui.add_provider_name_control, height=1)]),
            VSplit([Window(FormattedTextControl(" Base URL:         "), width=20), Window(ui.add_provider_url_control, height=1)]),
        ]), title="Adicionar Novo Provedor Customizado"), padding=1), filter=visible("add_provider")), left=8, right=8, top=4, height=10),
        Float(content=ConditionalContainer(Box(Frame(Window(ui.autonomy_control, wrap_lines=False, right_margins=[ScrollbarMargin()]), title="Permissions & Autonomy"), padding=1), filter=visible("autonomy_control")), left=6, right=6, top=2, bottom=3),
        Float(content=ConditionalContainer(Box(Frame(Window(ui.agents_control, wrap_lines=False, right_margins=[ScrollbarMargin()]), title="Agents Dashboard"), padding=1), filter=visible("agents")), left=6, right=6, top=2, bottom=3),
        Float(content=ConditionalContainer(Box(Frame(HSplit([
            Window(ui.provider_endpoint_help_control, height=3),
            VSplit([Window(FormattedTextControl(" Endpoint URL: "), width=16), Window(ui.provider_endpoint_control, height=1)]),
        ]), title="Configurar Endpoint Ollama / Remoto"), padding=1), filter=visible("provider_endpoint")), left=8, right=8, top=5, height=9),
        Float(content=ConditionalContainer(Box(Frame(Window(ui.help_control, wrap_lines=False, right_margins=[ScrollbarMargin()]), title="Help"), padding=1), filter=visible("help")), left=8, right=8, top=2, bottom=3),
        Float(content=ConditionalContainer(Frame(Window(ui.toast_control, height=4, wrap_lines=False), title="Notice [Esc/Enter: OK/Fechar]"), filter=Condition(lambda: bool(ui.state.active_toasts()))), right=1, top=1, width=60),
    ]
    return FloatContainer(content=content, floats=floats)
