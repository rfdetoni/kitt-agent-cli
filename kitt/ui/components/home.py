from kitt.ui.theme import DEFAULT_THEME
from kitt.ui.state import UIState


class HomeComponent:
    def render(self, state: UIState, width: int = 80) -> str:
        t = DEFAULT_THEME
        scanner = t.scanner_frame(state.scanner_step if hasattr(state, "scanner_step") else 0, width=16)
        logo = [
            t.format_primary("  ██╗  ██╗  ██╗.████████╗.████████╗  "),
            t.format_primary("  ██║ ██╔╝  ██║╚══██╔══╝╚══██╔══╝  "),
            t.format_primary("  █████═╝   ██║   ██║      ██║     "),
            t.format_primary("  ██╔═██╗   ██║   ██║      ██║     "),
            t.format_primary("  ██║  ██╗  ██║   ██║      ██║     "),
            f"       Scanner: [{t.format_primary(scanner)}]",
            t.format_muted("     K.I.T.T. Autonomous AI Coding Agent — Terminal State-of-the-Art"),
            "",
            t.format_muted(f" Workspace: {state.workspace_path}"),
            t.format_muted(f" Modelos  : {state.large_model} (Principal) │ {state.small_model} (Contexto)"),
            "",
            " [Ctrl+P] Paleta de Ações │ [/model] Modelos │ [/session] Sessões │ [/help] Ajuda │ [/doctor] Diagnóstico",
        ]
        return "\n".join(logo)

