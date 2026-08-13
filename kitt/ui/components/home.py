from kitt.ui.theme import DEFAULT_THEME
from kitt.ui.state import UIState

class HomeComponent:
    def render(self, state: UIState, width: int = 80) -> str:
        t = DEFAULT_THEME
        scanner = t.scanner_frame(0, width=16)
        logo = [
            t.format_primary("  ██╗  ██╗  ██╗.████████╗.████████╗  "),
            t.format_primary("  ██║ ██╔╝  ██║╚══██╔══╝╚══██╔══╝  "),
            t.format_primary("  █████═╝   ██║   ██║      ██║     "),
            t.format_primary("  ██╔═██╗   ██║   ██║      ██║     "),
            t.format_primary("  ██║  ██╗  ██║   ██║      ██║     "),
            f"       Scanner: [{t.format_primary(scanner)}]",
            t.format_muted("     K.I.T.T. Autonomous Agent — OpenCode Architecture"),
            "",
            t.format_muted(f" Workspace: {state.workspace_path}"),
            t.format_muted(f" Models   : {state.small_model} (Context/Filter) | {state.large_model} (Execution)"),
            "",
            " Shortcuts: [Ctrl+P] Palette | [/help] Commands | [/doctor] Health | [/quit] Exit",
        ]
        return "\n".join(logo)
