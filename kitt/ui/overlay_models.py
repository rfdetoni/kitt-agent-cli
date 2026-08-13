from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class OverlayFrame:
    name: str
    previous_focus: Any = None
    preferred_focus: Any = None


class SessionPickerModel:
    def __init__(self, runtime):
        self.runtime = runtime
        self.sessions: list[dict] = []
        self.selected_index: int = 0
        self.query: str = ""

    async def reload(self, query: str = "") -> None:
        self.query = query
        try:
            self.sessions = self.runtime.history.list_history(20, 0, query or None)
        except Exception:
            self.sessions = []
        self.selected_index = max(0, min(self.selected_index, max(0, len(self.sessions) - 1)))

    def move_selection(self, delta: int) -> None:
        if not self.sessions:
            self.selected_index = 0
            return
        self.selected_index = (self.selected_index + delta) % len(self.sessions)

    def get_selected(self) -> dict | None:
        if 0 <= self.selected_index < len(self.sessions):
            return self.sessions[self.selected_index]
        return None


class TimelineModel:
    def __init__(self, runtime):
        self.runtime = runtime
        self.turns: list[dict] = []
        self.selected_index: int = 0

    async def reload(self, conversation_id: str | None) -> None:
        if not conversation_id:
            self.turns = []
            return
        try:
            self.turns = self.runtime.history.list_turns(conversation_id)
        except Exception:
            self.turns = []
        self.selected_index = max(0, min(self.selected_index, max(0, len(self.turns) - 1)))

    def move_selection(self, delta: int) -> None:
        if not self.turns:
            self.selected_index = 0
            return
        self.selected_index = (self.selected_index + delta) % len(self.turns)


class DiffViewerModel:
    def __init__(self, workspace_path: str):
        self.workspace_path = workspace_path
        self.diff_text: str = ""
        self.files: list[str] = []
        self.scroll_offset: int = 0

    async def reload(self) -> None:
        def _get_diff():
            try:
                res = subprocess.run(["git", "diff"], cwd=self.workspace_path, capture_output=True, text=True, timeout=5)
                return res.stdout or "No uncommitted changes."
            except Exception as exc:
                return f"Failed to get diff: {exc}"
        self.diff_text = _get_diff()

    def scroll(self, delta: int) -> None:
        lines = self.diff_text.splitlines()
        self.scroll_offset = max(0, min(self.scroll_offset + delta, max(0, len(lines) - 10)))


class ModelSetupModel:
    """Focusable model-role picker. Persisting is owned by KittUIApp."""

    roles = ("principal", "context", "validation")
    providers = (
        "ollama", "lmstudio", "openai", "anthropic", "gemini", "deepseek",
        "groq", "together", "mistral", "openrouter", "xai", "fireworks",
        "cohere", "azure", "antigravity"
    )

    def __init__(self):
        self.models: list[str] = []
        self.role_index = 0
        self.model_index = 0
        self.provider_index = 0
        self.base_url_override: str | None = None

    @property
    def selected_role(self) -> str:
        return self.roles[self.role_index]

    @property
    def selected_model(self) -> str | None:
        return self.models[self.model_index] if self.models else None

    @property
    def selected_provider(self) -> str:
        return self.providers[self.provider_index]

    def move_role(self, delta: int) -> None:
        self.role_index = (self.role_index + delta) % len(self.roles)

    def move_model(self, delta: int) -> None:
        if self.models:
            self.model_index = (self.model_index + delta) % len(self.models)

    def move_provider(self, delta: int) -> None:
        self.provider_index = (self.provider_index + delta) % len(self.providers)
