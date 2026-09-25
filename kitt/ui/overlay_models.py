from __future__ import annotations

import subprocess
from dataclasses import dataclass
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


from kitt.ui.model_picker_state import ModelSelectionBehavior
from kitt.ui.provider_picker_state import PROVIDER_PATTERNS, ProviderPatternBehavior
from kitt.ui.provider_catalog_state import ProviderCatalogBehavior
from kitt.ui.provider_popup_state import ProviderPopupBehavior


class ModelSetupModel(
    ModelSelectionBehavior,
    ProviderPatternBehavior,
    ProviderCatalogBehavior,
    ProviderPopupBehavior,
):
    """Compatibility facade composing independent model and provider selection behaviors."""

    def __init__(self):
        self.models: list[str] = []
        self.role_index = 0
        self.model_index = 0
        self.provider_index = 0
        self.base_url_override: str | None = None
        self.favorite_providers: list[str] = [
            "kitt-reverse-proxy", "ollama", "openai", "anthropic", "gemini"
        ]
        self.custom_providers: list[dict] = []
        self.provider_popup_index = 1
        self.pattern_index = 0
        self.search_query = ""
        self._descriptors_cache: dict[tuple[str, str], str] = {}
        self._catalog = None
        self.loading = False
        self.error_message: str | None = None
        self.source = "api"
        self.pending_model_selection: tuple[str, str, str, str | None] | None = None
        self._popup_row_map: list[int | None] = []
