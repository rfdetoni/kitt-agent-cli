from __future__ import annotations

from kitt.ui.theme import DEFAULT_THEME
from kitt.ui.state import UIState


class ContextPanelComponent:
    """Compact single-line context feedback panel."""

    def render(self, state: UIState, width: int = 80) -> str:
        cs = state.context_stats
        t = DEFAULT_THEME
        if not cs.index_state and not cs.selected_count and not cs.filter_source:
            return ""

        # Glyph & color rules:
        # ◈ accent (#4CC9F0) when coverage == 1.0 and not degraded
        # ◈ warning when degraded (coverage < 1.0 or index_state == "PARTIAL")
        # ◈ error when index_state == "DEGRADED" or filter_source == "FALLBACK"
        if cs.index_state == "DEGRADED" or (cs.filter_source == "FALLBACK" and cs.filter_fallback_reason):
            glyph = t.format_error("◈")
        elif cs.degraded or cs.coverage < 1.0 or cs.index_state == "PARTIAL":
            glyph = t.format_warning("◈")
        else:
            glyph = f"\033[38;2;76;201;240m◈\033[0m" if not t.format_primary("x").startswith("x") else "◈"

        idx_str = f"idx:{cs.index_state or 'READY'}"
        if cs.index_generation:
            idx_str += f"(gen {cs.index_generation})"

        total = cs.selected_count + cs.rejected_count
        sel_str = f"sel {cs.selected_count}/{total}" if total > 0 else f"sel {cs.selected_count}"
        cov_str = f"cov {cs.coverage:.0%}"

        tok_k = f"{cs.context_tokens / 1000:.1f}k" if cs.context_tokens else "0k"
        tok_str = f"{tok_k} tok"

        filt_str = f"filtro:{cs.filter_source or 'N/A'}"
        if cs.filter_latency_ms > 0:
            filt_str += f"({int(cs.filter_latency_ms)}ms)"

        parts = [f"{glyph} Contexto", idx_str, sel_str, cov_str, tok_str, filt_str]
        line = "  ".join(parts)
        # Strip ANSI codes for visible width check
        import re
        visible_len = len(re.sub(r"\x1b\[[0-9;]*m", "", line))
        if visible_len > width:
            line = f"{glyph} Ctx: {cs.selected_count}/{total} ({cs.coverage:.0%}) {idx_str}"
        return line
