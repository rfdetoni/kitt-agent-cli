from kitt.ui.commands import CommandRegistry
from kitt.ui.theme import DEFAULT_THEME

class CommandPaletteComponent:
    def __init__(self, registry: CommandRegistry):
        self.registry = registry

    def render(self, query: str = "", width: int = 80) -> str:
        t = DEFAULT_THEME
        cmds = self.registry.search(query)
        lines = [t.format_primary("─── COMMAND PALETTE (Type to filter) ───")]
        for c in cmds[:8]:
            aliases = ", ".join(c.aliases)
            lines.append(f"  {c.id:<18} [{aliases:<12}] - {c.description}")
        return "\n".join(lines)
