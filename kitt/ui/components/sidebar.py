from kitt.ui.theme import DEFAULT_THEME
from kitt.ui.state import UIState

class SidebarComponent:
    def render(self, state: UIState, width: int = 40) -> str:
        t = DEFAULT_THEME
        lines = [
            t.format_primary("─── SYSTEM METRICS ───"),
            f" Workspace : {state.workspace_name[:25]}",
            f" Conversation: {(state.active_conversation_id or 'None')[:20]}",
            f" Tokens Used : {state.tokens_used}",
            f" Net Saved   : {state.net_saved_tokens}",
            "",
            t.format_primary("─── AGENTES & TAREFAS ───"),
        ]

        if state.active_tasks:
            pct = state.overall_progress
            filled = (pct * 12) // 100
            bar = "█" * filled + "░" * (12 - filled)
            lines.append(f" Progresso : [{t.format_primary(bar)}] {pct}%")
            lines.append("")

            for task in state.active_tasks:
                if task.status == "running":
                    icon = "⚡"
                    status_str = t.format_primary("RUN")
                elif task.status == "done":
                    icon = "✔"
                    status_str = t.format_muted("OK ")
                elif task.status == "error":
                    icon = "✖"
                    status_str = t.format_error("ERR")
                else:
                    icon = "⏳"
                    status_str = t.format_muted("WAIT")

                lines.append(f" {icon} {task.name[:18]:<18} [{status_str}]")
                if task.summary:
                    lines.append(t.format_muted(f"   ↳ {task.summary[:32]}"))
        else:
            lines.append(t.format_muted(" Nenhum agente em execução"))

        lines.extend([
            "",
            t.format_primary("─── ACTIVE MODELS ───"),
            f" Small (Filter): {state.small_model[:20]}",
            f" Large (Exec)  : {state.large_model[:20]}",
            "",
            t.format_primary("─── ACTIVE OVERLAY ───"),
            f" Overlay State : {state.active_overlay or 'None'}",
        ])
        return "\n".join(lines)
