import time
from kitt.ui.theme import DEFAULT_THEME
from kitt.ui.state import UIState

class PermissionCardComponent:
    def render(self, state: UIState, width: int = 88) -> str:
        t = DEFAULT_THEME
        pending = state.pending_approvals
        if not pending and state.pending_approval:
            pending = [state.pending_approval]
        if not pending:
            return t.format_muted(" APPROVAL REQUIRED: Nenhuma aprovação pendente.")
        req = pending[0]
        queue_note = f" (1 de {len(pending)} na fila)" if len(pending) > 1 else ""
        tool_name = req.get("tool_name", "Unknown")
        args = req.get("args", {})
        affected_paths = req.get("affected_paths", [])
        diff_preview = req.get("diff_preview", "")
        expires_at = req.get("expires_at", 0)
        expires_in = max(0, int(expires_at - time.time())) if expires_at else 300

        lines = [
            t.format_primary(f"┌── APROVAÇÃO NECESSÁRIA / APPROVAL REQUIRED{queue_note} ─── expira em {expires_in}s ─┐"),
            f"│ Ferramenta: {tool_name:<30} │",
        ]
        if affected_paths:
            lines.append(f"│ Arquivos afetados: {', '.join(affected_paths)[:60]:<60} │")
        if diff_preview:
            lines.append(t.format_muted("│ ---- diff ----"))
            for dl in diff_preview.splitlines()[:12]:
                lines.append(f"│ {dl[:width-4]}")
        else:
            lines.append(f"│ Args: {str(args)[:60]:<60} │")
        lines.append(t.format_primary("└" + "─" * (width - 2) + "┘"))
        lines.append(" [y] Permitir uma vez  [A] Sempre permitir (workspace)  "
                      "[s] Sempre nesta sessão  [n] Negar  [d] Ver diff completo  [N] Negar todas na fila")
        return "\n".join(lines)
