from kitt.ui.theme import DEFAULT_THEME
from kitt.ui.state import UIState

class StatusBarComponent:
    def render(self, state: UIState, width: int = 80) -> str:
        t = DEFAULT_THEME
        left = f" Workspace: {state.workspace_name}"
        
        running = [tk for tk in state.active_tasks if tk.status == "running"]
        if len(running) > 1:
            center = f"[{len(running)} AGENTES EM EXECUÇÃO — Ctrl+X A for dashboard]"
        elif len(running) == 1:
            center = f"[{running[0].name}: {running[0].progress}%]"
        elif state.status_text.startswith("✔") or "COMPLETED" in state.status_text:
            center = f"[✔ PROCESSO CONCLUÍDO COM SUCESSO]"
        elif state.status_text.startswith("✖") or "ERROR" in state.status_text or "FAILED" in state.status_text:
            center = f"[✖ FALHA NO PROCESSO]"
        else:
            center = f"[{state.status_text}]"

        cs = state.context_stats
        total_ctx = cs.selected_count + cs.rejected_count
        if total_ctx > 0:
            ctx_part = f"Ctx: {cs.selected_count}/{total_ctx} ({cs.coverage:.0%}) | "
        elif cs.index_state:
            ctx_part = f"Ctx: {cs.index_state} | "
        else:
            ctx_part = ""

        right = f"{ctx_part}🧠 {state.reasoning_effort}% (Ctrl+←/→) | Tokens: {state.tokens_used} | Saved: {state.net_saved_tokens} "
        
        # Balance space across width
        spaces = max(1, width - len(left) - len(center) - len(right))
        pad_l = spaces // 2
        pad_r = spaces - pad_l
        
        line = left + (" " * pad_l) + t.format_primary(center) + (" " * pad_r) + t.format_muted(right)
        return line
