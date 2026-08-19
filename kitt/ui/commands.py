from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class CommandSpec:
    id: str
    title: str
    category: str
    description: str
    aliases: List[str]
    handler_name: str = ""


class CommandRegistry:
    """Single slash-command catalog used by editor, palette and help."""

    def __init__(self):
        self.commands: Dict[str, CommandSpec] = {}
        self._register_defaults()

    def register(self, spec: CommandSpec):
        self.commands[spec.id] = spec

    def _register_defaults(self):
        rows = [
            ("new", "New Conversation", "Session", "Start a new persistent conversation", ["/new"]),
            ("history", "History", "Session", "Show messages in current conversation", ["/history"]),
            ("thread", "Conversations", "Session", "List and search conversations", ["/thread", "/conversations"]),
            ("resume", "Resume Conversation", "Session", "Resume conversation by index or id", ["/resume", "/continue"]),
            ("conversation", "Conversation Info", "Session", "Show active conversation details", ["/conversation"]),
            ("fork", "Fork Conversation", "Session", "Fork active conversation", ["/fork"]),
            ("export", "Export Conversation", "Session", "Export conversa para Markdown ou JSON", ["/export", "/export-conversation"]),
            ("doctor", "Diagnostics", "System", "Run environment diagnostics", ["/doctor"]),
            ("add", "Add Files", "Context", "Add files to active context", ["/add"]),
            ("drop", "Drop Files", "Context", "Remove files from active context", ["/drop"]),
            ("files", "Context Files", "Context", "List active context files", ["/files", "/ls"]),
            ("memory", "Memory", "Memory", "Show persistent project and global memory (/memory [list|inspect|stats|forget])", ["/memory"]),
            ("remember", "Remember", "Memory", "Add a project memory guideline", ["/remember"]),
            ("clear_memory", "Clear Memory", "Memory", "Reset project memory guidelines", ["/clear-memory"]),
            ("dream", "Dreaming Mode", "Memory", "Consolidate long-term memory (/dream [status|inspect|run|cancel])", ["/dream"]),
            ("skills", "Skills", "Skills", "List installed agent skills", ["/skills"]),
            ("setup_skills", "Setup Skills", "Skills", "Configure active skills", ["/setup-skills"]),
            ("skill_install", "Install Skill", "Skills", "Install skill from GitHub or Git URL", ["/skill-install"]),
            ("skill_remove", "Remove Skill", "Skills", "Remove installed skill", ["/skill-remove"]),
            ("repomap", "Repository Map", "Context", "Show repository symbol map", ["/repomap"]),
            ("diff", "Git Diff", "Git", "Show uncommitted changes", ["/diff"]),
            ("commit", "Commit", "Git", "Create a Git commit", ["/commit"]),
            ("undo", "Undo", "Git", "Revert last K.I.T.T. changeset", ["/undo"]),
            ("run", "Run Command", "Tools", "Run a command through policy", ["/run"]),
            ("ask", "Ask", "Turn", "Ask without code edits", ["/ask"]),
            ("plan", "Plan Mode", "Turn", "Toggle planning mode or generate execution plan", ["/plan"]),
            ("code", "Code", "Turn", "Force code-editing mode", ["/code"]),
            ("mode", "Turn Mode", "Turn", "Toggle or set mode: /mode [code|plan|ask] (F4 / Ctrl+T)", ["/mode", "/toggle-mode"]),
            ("model", "Model", "Models", "Set one role or all roles: /model <role|all> [provider] <model>", ["/model", "/models"]),
            ("setup_models", "Setup Models", "Models", "Configure models; optional remote Ollama URL", ["/setup-models"]),
            ("add_provider", "Add Provider", "Models", "Register custom provider template: /add-provider <name> [ollama|openai|anthropic|gemini] <url>", ["/add-provider", "/add-server"]),
            ("edit_provider", "Edit Provider", "Models", "Edit custom provider: /edit-provider <name> [new_url] [pattern] [token]", ["/edit-provider"]),
            ("delete_provider", "Delete Provider", "Models", "Delete custom provider: /delete-provider <name>", ["/delete-provider", "/remove-provider"]),
            ("reasoning", "Reasoning Effort", "Models", "Adjust thinking depth: /reasoning <0-100> (Ctrl+Left/Right)", ["/reasoning", "/think", "/effort"]),
            ("router", "Router", "Models", "Show task routing configuration", ["/router"]),
            ("context_stats", "Context Stats", "Analytics", "Show context budget telemetry", ["/context-stats"]),
            ("stats", "Telemetry Stats", "Analytics", "Show token and latency telemetry", ["/stats", "/metrics"]),
            ("status", "Runtime Status", "System", "Show runtime snapshot", ["/status"]),
            ("compact", "Compact History", "Session", "Compact bounded conversation history", ["/compact"]),
            ("child", "Child Agent", "Agents", "Spawn isolated child task", ["/child"]),
            ("child_inspect", "Inspect Child", "Agents", "Inspect child agent state and artifacts: /child-inspect <id>", ["/child-inspect", "/inspect-child"]),
            ("child_message", "Message Child", "Agents", "Send structured message to child agent: /child-msg <id> <message>", ["/child-msg", "/child-message"]),
            ("child_retain", "Retain Child", "Agents", "Retain child agent for reuse: /child-retain <id>", ["/child-retain", "/retain-child"]),
            ("tasks", "Agent Task Monitor", "Agents", "Show active subagent tasks & progress", ["/tasks", "/task", "/agents", "/agent"]),
            ("goal_pause", "Pause Goal", "Goals", "Pause active autonomous goal: /goal-pause <id>", ["/goal-pause"]),
            ("goal_resume", "Resume Goal", "Goals", "Resume paused goal: /goal-resume <id>", ["/goal-resume"]),
            ("attach", "Attach Session", "Daemon", "Attach to background daemon session: /attach <id>", ["/attach"]),
            ("detach", "Detach Session", "Daemon", "Detach from current daemon session: /detach", ["/detach"]),
            ("runtime_state", "Runtime State", "System", "Inspect persistent session runtime state: /runtime-state [list|get]", ["/runtime-state", "/state"]),
            ("artifact", "Open Artifact", "Context", "Read or inspect persisted artifact: /artifact <id>", ["/artifact", "/art"]),
            ("cancel", "Cancel Turn", "Turn", "Cancel active turn or background operations", ["/cancel", "/stop"]),
            ("approvals", "Approvals", "Security", "Show approval audit trail", ["/approvals"]),
            ("autonomy", "Autonomy Profile", "Security", "Set autonomy level: /autonomy <read_only|supervised|balanced|autonomous>", ["/autonomy"]),
            ("workspace", "Workspace", "System", "Show or switch workspace", ["/workspace"]),
            ("mouse", "Toggle Mouse Mode", "System", "Toggle between TUI mouse and Terminal native selection (F10 / Ctrl+M)", ["/mouse"]),
            ("clear", "Clear Context", "Session", "Start clean conversation context", ["/clear"]),
            ("help", "Help", "System", "Show all slash commands", ["/help", "/"]),
            ("quit", "Exit K.I.T.T.", "System", "Exit application", ["/quit", "/exit"]),
        ]
        for command_id, title, category, description, aliases in rows:
            self.register(CommandSpec(command_id, title, category, description, aliases, f"cmd_{command_id}"))

    def search(self, query: str) -> List[CommandSpec]:
        q = query.lower().strip()
        if not q or q == "/":
            return list(self.commands.values())
        if q.startswith("/"):
            q_term = q[1:].strip()
        else:
            q_term = q

        tokens = q_term.split() if q_term else [q]
        scored: List[tuple[int, CommandSpec]] = []

        for command in self.commands.values():
            cmd_id = command.id.lower()
            title = command.title.lower()
            desc = command.description.lower()
            cat = command.category.lower()
            aliases = [a.lower() for a in command.aliases]
            all_text = f"{cmd_id} {title} {desc} {cat} {' '.join(aliases)}"

            if all(tok in all_text for tok in tokens):
                score = 0
                if any(q == a or q_term == a.lstrip("/") for a in aliases):
                    score += 100
                elif cmd_id == q_term:
                    score += 90
                elif any(a.startswith(q) or a.lstrip("/").startswith(q_term) for a in aliases):
                    score += 60
                elif cmd_id.startswith(q_term):
                    score += 50
                elif any(tok in title for tok in tokens):
                    score += 30
                elif any(tok in cat for tok in tokens):
                    score += 20
                else:
                    score += 10
                scored.append((score, command))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [item[1] for item in scored]

    def find(self, name: str) -> CommandSpec | None:
        name = name.lower().strip()
        return next((command for command in self.commands.values() if name in command.aliases or name == command.id or name == f"/{command.id}"), None)
