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
            ("export_conversation", "Export Conversation", "Session", "Export conversation as Markdown or JSON", ["/export-conversation"]),
            ("doctor", "Diagnostics", "System", "Run environment diagnostics", ["/doctor"]),
            ("add", "Add Files", "Context", "Add files to active context", ["/add"]),
            ("drop", "Drop Files", "Context", "Remove files from active context", ["/drop"]),
            ("files", "Context Files", "Context", "List active context files", ["/files", "/ls"]),
            ("memory", "Memory", "Memory", "Show persistent project and global memory", ["/memory"]),
            ("remember", "Remember", "Memory", "Add a project memory guideline", ["/remember"]),
            ("clear_memory", "Clear Memory", "Memory", "Reset project memory guidelines", ["/clear-memory"]),
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
            ("code", "Code", "Turn", "Force code-editing mode", ["/code"]),
            ("model", "Model", "Models", "Set one role or all roles: /model <role|all> [provider] <model>", ["/model", "/models"]),
            ("setup_models", "Setup Models", "Models", "Configure models; optional remote Ollama URL", ["/setup-models"]),
            ("router", "Router", "Models", "Show task routing configuration", ["/router"]),
            ("context_stats", "Context Stats", "Analytics", "Show context budget telemetry", ["/context-stats"]),
            ("stats", "Telemetry Stats", "Analytics", "Show token and latency telemetry", ["/stats", "/metrics"]),
            ("status", "Runtime Status", "System", "Show runtime snapshot", ["/status"]),
            ("compact", "Compact History", "Session", "Compact bounded conversation history", ["/compact"]),
            ("child", "Child Agent", "Agents", "Spawn isolated child task", ["/child"]),
            ("tasks", "Agent Task Monitor", "Agents", "Show active subagent tasks & progress", ["/tasks", "/agents"]),
            ("approvals", "Approvals", "Security", "Show approval audit trail", ["/approvals"]),
            ("autonomy", "Autonomy Profile", "Security", "Set autonomy level: /autonomy <read_only|supervised|balanced|autonomous>", ["/autonomy"]),
            ("workspace", "Workspace", "System", "Show or switch workspace", ["/workspace"]),
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
        matches = [
            command for command in self.commands.values()
            if q in command.id.lower()
            or q in command.title.lower()
            or q in command.description.lower()
            or any(q in alias.lower() for alias in command.aliases)
        ]
        return sorted(matches, key=lambda command: 0 if q == command.id.lower() or q in command.aliases else 1)

    def find(self, name: str) -> CommandSpec | None:
        name = name.lower()
        return next((command for command in self.commands.values() if name in command.aliases or name == command.id), None)
