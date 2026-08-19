from kitt.ui.theme import DEFAULT_THEME
from kitt.ui.state import UIState


class SidebarComponent:
    def render(self, state: UIState, width: int = 40) -> str:
        t = DEFAULT_THEME
        lines = [
            t.format_primary("─── SYSTEM METRICS / WORKSPACE ───"),
            f"  {state.workspace_name[:width-4]}",
            f"  Sessão: {(state.active_conversation_id or 'Nenhuma')[:width-12]}",
            "",
            t.format_primary("─── MODELOS ATIVOS ───"),
            f"  Principal: {state.large_model[:width-14]}",
            f"  Contexto : {state.small_model[:width-14]}",
            "",
            t.format_primary("─── CONTEXTO & TOKENS ───"),
        ]

        # Context window bar
        ctx_used = state.tokens_used
        ctx_max = max(1, state.context_window)
        ctx_pct = min(100, max(0, (ctx_used * 100) // ctx_max))
        bar_len = 10
        filled = (ctx_pct * bar_len) // 100
        bar = "█" * filled + "░" * (bar_len - filled)
        lines.append(f"  [{t.format_primary(bar)}] {ctx_pct}% ({ctx_used}/{ctx_max})")
        if state.net_saved_tokens > 0:
            lines.append(t.format_muted(f"  Economizados: {state.net_saved_tokens} tokens"))

        lines.extend([
            "",
            t.format_primary("─── AGENTES & TAREFAS ───"),
        ])

        if state.active_tasks:
            pct = state.overall_progress
            filled = (pct * 10) // 100
            tbar = "█" * filled + "░" * (10 - filled)
            lines.append(f"  Progresso: [{t.format_primary(tbar)}] {pct}%")
            lines.append("")

            for task in state.active_tasks:
                if task.status == "running":
                    icon = "⚡"
                    status_str = t.format_primary("EXEC")
                elif task.status == "done":
                    icon = "✔"
                    status_str = t.format_muted("OK  ")
                elif task.status == "error":
                    icon = "✖"
                    status_str = t.format_error("ERRO")
                else:
                    icon = "⏳"
                    status_str = t.format_muted("AGUARD")

                lines.append(f"  {icon} {task.name[:16]:<16} [{status_str}]")
                if task.summary:
                    lines.append(t.format_muted(f"     ↳ {task.summary[:width-8]}"))
        else:
            lines.append(t.format_muted("  Nenhum agente em execução"))

        if state.active_overlay:
            lines.extend([
                "",
                t.format_muted(f"  Overlay ativo: {state.active_overlay}"),
            ])

        return "\n".join(lines)

