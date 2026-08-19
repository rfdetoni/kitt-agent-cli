import time
from kitt.ui.theme import DEFAULT_THEME
from kitt.ui.state import UIState


class StatusBarComponent:
    def render(self, state: UIState, width: int = 80) -> str:
        t = DEFAULT_THEME

        # Priority 1: Pending Permissions
        if state.pending_approvals:
            count = len(state.pending_approvals)
            center = f"[⚠ APROVAÇÃO NECESSÁRIA — {count} pendente(s) — Tab/y/n]"
        # Priority 2: Active Running Tasks
        elif state.active_tasks:
            running = [tk for tk in state.active_tasks if tk.status == "running"]
            if len(running) > 1:
                center = f"[{len(running)} AGENTES EM EXECUÇÃO — Ctrl+X A Dashboard]"
            elif len(running) == 1:
                elapsed = int(time.time() - running[0].started_at)
                center = f"[◌ {running[0].name}: {running[0].progress}% · {elapsed}s]"
            elif state.status_text.startswith("✔") or "COMPLETED" in state.status_text:
                center = "[✔ CONCLUÍDO COM SUCESSO]"
            elif state.status_text.startswith("✖") or "ERROR" in state.status_text or "FAILED" in state.status_text:
                center = "[✖ FALHA NO PROCESSO]"
            else:
                center = f"[{state.status_text}]"
        elif state.status_text.startswith("✖") or "ERROR" in state.status_text or "FAILED" in state.status_text:
            center = "[✖ FALHA NO PROCESSO]"
        elif state.status_text and state.status_text != "SYSTEM ONLINE":
            center = f"[{state.status_text}]"
        else:
            center = f"[● Online | {state.large_model}]"

        # Context summary
        cs = state.context_stats
        total_ctx = cs.selected_count + cs.rejected_count
        if total_ctx > 0:
            ctx_part = f"Ctx: {cs.selected_count}/{total_ctx} ({cs.coverage:.0%}) | "
        elif cs.index_state:
            ctx_part = f"Ctx: {cs.index_state} | "
        else:
            ctx_part = ""

        # Narrow / Mobile mode (< 60 chars)
        mode_tag = state.turn_mode.upper() if hasattr(state, "turn_mode") else "CODE"
        if width < 60:
            return t.format_primary(f" {center[:width-2]}")

        left = f" ⬡ {state.workspace_name[:16]} │ F4: {mode_tag} │ F12: Modelos"
        right = f"{ctx_part}🧠 {state.reasoning_effort}% │ Ctrl+P "

        # Space balancing
        needed = len(left) + len(center) + len(right)
        if needed <= width:
            spaces = width - needed
            pad_l = spaces // 2
            pad_r = spaces - pad_l
            line = left + (" " * pad_l) + t.format_primary(center) + (" " * pad_r) + t.format_muted(right)
        else:
            # Fallback if wide
            compact_left = f" ⬡ F4: {mode_tag} │ F12"
            compact_right = f"{ctx_part}Ctrl+P "
            spaces = max(1, width - len(compact_left) - len(center) - len(compact_right))
            line = compact_left + (" " * (spaces // 2)) + t.format_primary(center) + (" " * (spaces - spaces // 2)) + t.format_muted(compact_right)

        return line

