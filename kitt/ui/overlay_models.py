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


class _ProvidersProperty:
    def __get__(self, instance, owner):
        if instance is None:
            return owner.default_providers
        custom_names = [cp["name"] for cp in instance.custom_providers if cp["name"] not in instance.default_providers]
        ordered = []
        for f in instance.favorite_providers:
            if f not in ordered:
                ordered.append(f)
        for p in instance.default_providers:
            if p not in ordered:
                ordered.append(p)
        for c in custom_names:
            if c not in ordered:
                ordered.append(c)
        return tuple(ordered)


class ModelSetupModel:
    """Focusable model-role picker with favorites and provider dropdown support."""

    roles = ("principal", "context", "validation")
    default_providers = (
        "ollama", "lmstudio", "openai", "anthropic", "gemini", "deepseek",
        "groq", "together", "mistral", "openrouter", "xai", "fireworks",
        "cohere", "azure", "antigravity"
    )
    providers = _ProvidersProperty()

    def __init__(self):
        self.models: list[str] = []
        self.role_index = 0
        self.model_index = 0
        self.provider_index = 0
        self.base_url_override: str | None = None
        self.favorite_providers: list[str] = ["ollama", "openai", "anthropic", "gemini"]
        self.custom_providers: list[dict] = []
        self.provider_popup_index: int = 1
        self.search_query: str = ""
        self._descriptors_cache: dict[tuple[str, str], str] = {}
        self._catalog = None

    @property
    def selected_role(self) -> str:
        return self.roles[self.role_index]

    def get_filtered_models(self) -> list[str]:
        if not self.search_query.strip():
            return self.models
        tokens = self.search_query.lower().split()
        return [m for m in self.models if all(tok in m.lower() for tok in tokens)]

    @property
    def selected_model(self) -> str | None:
        filtered = self.get_filtered_models()
        if not filtered:
            return None
        if self.model_index >= len(filtered):
            self.model_index = 0
        return filtered[self.model_index]

    @property
    def selected_provider(self) -> str:
        provs = self.providers
        if self.provider_index >= len(provs):
            self.provider_index = 0
        return provs[self.provider_index]

    def move_role(self, delta: int) -> None:
        self.role_index = (self.role_index + delta) % len(self.roles)

    def move_model(self, delta: int) -> None:
        filtered = self.get_filtered_models()
        if filtered:
            self.model_index = (self.model_index + delta) % len(filtered)

    def format_model_badge(self, provider: str, model_id: str) -> str:
        key = (provider.lower(), model_id)
        if key in self._descriptors_cache:
            return self._descriptors_cache[key]

        badge_parts = []
        try:
            if self._catalog is None:
                from kitt.llm.catalog import ProviderCatalogService
                self._catalog = ProviderCatalogService()
            desc = self._catalog.model(provider, model_id)
            if desc:
                # Context limit
                if desc.context_window:
                    if desc.context_window >= 1_000_000:
                        ctx_str = f"{desc.context_window / 1_000_000:.1f}M ctx".replace(".0M", "M")
                    elif desc.context_window >= 1000:
                        ctx_str = f"{desc.context_window // 1000}k ctx"
                    else:
                        ctx_str = f"{desc.context_window} ctx"
                    badge_parts.append(ctx_str)
                if desc.supports_tools:
                    badge_parts.append("🛠 tools")
                if desc.supports_reasoning:
                    badge_parts.append("🧠 think")
                if "image" in desc.input_modalities:
                    badge_parts.append("👁 vision")
        except Exception:
            pass

        badge = f" │ {' │ '.join(badge_parts)}" if badge_parts else ""
        self._descriptors_cache[key] = badge
        return badge

    def move_provider(self, delta: int) -> None:
        provs = self.providers
        self.provider_index = (self.provider_index + delta) % len(provs)
        self.model_index = 0
        self.search_query = ""

    def toggle_favorite(self, provider: str) -> bool:
        if provider in self.favorite_providers:
            self.favorite_providers.remove(provider)
            return False
        else:
            self.favorite_providers.append(provider)
            return True

    def add_custom_provider(self, name: str, base_url: str, backend: str = "openai") -> None:
        name = name.strip().lower()
        if not name:
            return
        self.custom_providers = [cp for cp in self.custom_providers if cp["name"] != name]
        self.custom_providers.append({"name": name, "base_url": base_url.strip(), "backend": backend})
        if name not in self.favorite_providers:
            self.favorite_providers.append(name)
        provs = self.providers
        if name in provs:
            self.provider_index = provs.index(name)

    def get_popup_entries(self) -> list[dict]:
        entries = []
        entries.append({"kind": "header", "title": "⭐ PROVEDORES FAVORITOS", "id": ""})
        for f in self.favorite_providers:
            entries.append({"kind": "provider", "name": f, "is_favorite": True, "id": f})

        others = [p for p in self.providers if p not in self.favorite_providers]
        if others:
            entries.append({"kind": "header", "title": "🌐 TODOS OS PROVEDORES", "id": ""})
            for o in others:
                entries.append({"kind": "provider", "name": o, "is_favorite": False, "id": o})

        entries.append({"kind": "header", "title": "➕ AÇÕES", "id": ""})
        entries.append({"kind": "action", "name": "add_provider", "title": "[+ Adicionar Novo Provedor Customizado]", "id": "add_provider"})
        return entries

    def get_selectable_indices(self) -> list[int]:
        entries = self.get_popup_entries()
        return [idx for idx, e in enumerate(entries) if e["kind"] in ("provider", "action")]

    def move_popup_selection(self, delta: int) -> None:
        selectables = self.get_selectable_indices()
        if not selectables:
            return
        curr_selectable_pos = 0
        if self.provider_popup_index in selectables:
            curr_selectable_pos = selectables.index(self.provider_popup_index)
        next_pos = (curr_selectable_pos + delta) % len(selectables)
        self.provider_popup_index = selectables[next_pos]

    def get_selected_popup_entry(self) -> dict | None:
        entries = self.get_popup_entries()
        if 0 <= self.provider_popup_index < len(entries):
            return entries[self.provider_popup_index]
        return None

    def handle_mouse_hover(self, visual_row: int) -> None:
        filtered = self.get_filtered_models()
        if not filtered:
            return
        total = len(filtered)
        window_size = 14
        start = min(max(0, self.model_index - (window_size // 2)), max(0, total - window_size))
        offset = 1 if start > 0 else 0
        idx = start + max(0, visual_row - offset)
        if 0 <= idx < total:
            self.model_index = idx

    def handle_popup_mouse_hover(self, visual_row: int) -> None:
        entries = self.get_popup_entries()
        total = len(entries)
        window_size = 25
        start = min(max(0, self.provider_popup_index - (window_size // 2)), max(0, total - window_size))
        offset = 1 if start > 0 else 0
        idx = start + max(0, visual_row - offset)
        if 0 <= idx < total and entries[idx]["kind"] in ("provider", "action"):
            self.provider_popup_index = idx
