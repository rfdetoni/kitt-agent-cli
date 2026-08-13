from kitt.ui.theme import DEFAULT_THEME
from kitt.ui.state import UIState

class AgentsDashboardComponent:
    """Uma linha por agente ativo, cada uma com seu próprio farol K.I.T.T.
    deslocado em fase — efeito visual de múltiplos scanners simultâneos,
    igual ao carro original quando processa vários sistemas ao mesmo tempo."""

    STATUS_GLYPH = {"pending": "○", "running": "●", "done": "✔", "error": "✖", "cancelled": "∅"}

    def render(self, state: UIState, width: int = 88) -> str:
        t = DEFAULT_THEME
        if not state.active_tasks:
            return t.format_muted(" Nenhum agente ativo.")
        lines = [t.format_primary(f"┌── AGENTES ATIVOS ({state.active_agent_count()}) " + "─" * max(0, width - 26) + "┐")]
        for task in state.active_tasks:
            bar_width = 18
            step = state.scanner_step + task.scanner_phase
            bar = t.scanner_frame(step, bar_width) if task.status == "running" else " " * bar_width
            glyph = self.STATUS_GLYPH.get(task.status, "?")
            tag = "[CHILD]" if task.kind == "child_agent" else "[CORE]"
            label = f"{glyph} {tag} {task.name:<24}"
            row = f"│ {label} [{bar}] {task.progress:>3}%  {task.summary[:28]:<28} │"
            colored = t.format_primary(row) if task.status == "running" else \
                      (t.format_muted(row) if task.status in {"pending", "cancelled"} else row)
            lines.append(colored)
        lines.append(t.format_primary("└" + "─" * (width - 2) + "┘"))
        return "\n".join(lines)
