from __future__ import annotations

from typing import List, Optional
from kitt.ui.commands import CommandRegistry, CommandSpec
from kitt.ui.theme import DEFAULT_THEME


class CommandPaletteComponent:
    def __init__(self, registry: CommandRegistry):
        self.registry = registry

    def render(
        self,
        query: str = "",
        selected_index: int = 0,
        width: int = 80,
        window_size: int = 10,
    ) -> str:
        t = DEFAULT_THEME
        cmds: List[CommandSpec] = self.registry.search(query)
        if not cmds:
            if query.strip():
                return (
                    f"  Nenhum comando encontrado para '{query}'.\n"
                    "  Limpe a busca ou tente outro termo (ex: model, session, diff, auth, agent)."
                )
            return "  Nenhum comando registrado no sistema."

        total = len(cmds)
        start = min(max(0, selected_index - (window_size // 2)), max(0, total - window_size))
        end = min(total, start + window_size)
        visible = cmds[start:end]

        lines: List[str] = []
        if start > 0:
            lines.append(t.format_muted(f"  ▲ ... ({start} comandos acima)"))

        for i, c in enumerate(visible):
            idx = start + i
            is_selected = (idx == selected_index)
            cursor = ">" if is_selected else " "
            alias_str = f"[{c.aliases[0]}]"
            cat_str = f"[{c.category.upper()}]"
            line1 = f"{cursor} [{idx+1}/{total}] {c.title:<28} {cat_str:<12} {alias_str}"
            line2 = f"    {c.description}"
            if is_selected:
                lines.append(t.format_primary(line1))
                lines.append(t.format_primary(line2))
            else:
                lines.append(line1)
                lines.append(t.format_muted(line2))

        if end < total:
            lines.append(t.format_muted(f"  ▼ ... ({total - end} comandos abaixo)"))

        return "\n".join(lines)

