from dataclasses import dataclass, field
from typing import Dict, List

@dataclass
class KeyBinding:
    action: str
    keys: List[str]
    description: str

class KeyMap:
    def __init__(self):
        self.bindings: Dict[str, KeyBinding] = {
            "palette": KeyBinding("palette", ["c-p"], "Open Command Palette"),
            "new_session": KeyBinding("new_session", ["c-x n"], "Start New Session"),
            "sessions": KeyBinding("sessions", ["c-x l"], "List / Pick Sessions"),
            "toggle_sidebar": KeyBinding("toggle_sidebar", ["c-x b"], "Toggle Sidebar"),
            "status": KeyBinding("status", ["c-x s"], "Show System Status"),
            "context_details": KeyBinding("context_details", ["c-x c"], "Show Context Details"),
            "timeline": KeyBinding("timeline", ["c-x g"], "Show Session Timeline"),
            "models": KeyBinding("models", ["c-x m"], "Switch Models"),
            "agents": KeyBinding("agents", ["c-x a", "a"], "Open Agents Dashboard"),
            "external_editor": KeyBinding("external_editor", ["c-x e"], "Open External Editor"),
            "toggle_collapse": KeyBinding("toggle_collapse", ["c-o"], "Expandir/recolher último bloco de ferramenta"),
            "cancel": KeyBinding("cancel", ["escape", "c-c"], "Cancel Execution or Overlay"),
        }

    def get_help_list(self) -> List[tuple[str, str, str]]:
        return [(kb.action, ", ".join(kb.keys), kb.description) for kb in self.bindings.values()]
