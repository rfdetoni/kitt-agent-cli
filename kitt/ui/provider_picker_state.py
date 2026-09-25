from __future__ import annotations

from typing import Optional


PROVIDER_PATTERNS = [
    {
        "id": "ollama",
        "label": "Ollama (Servidor Local/Remoto - /api/tags)",
        "protocol": "ollama-chat",
        "default_backend": "ollama",
        "default_url": "http://localhost:11434",
    },
    {
        "id": "openai",
        "label": "OpenAI-Compatível (vLLM / LM Studio / LocalAI / LiteLLM)",
        "protocol": "openai-chat-completions",
        "default_backend": "openai",
        "default_url": "http://localhost:8000/v1",
    },
    {
        "id": "anthropic",
        "label": "Anthropic-Compatível (Claude Proxy / Bedrock)",
        "protocol": "anthropic-messages",
        "default_backend": "anthropic",
        "default_url": "https://api.anthropic.com",
    },
    {
        "id": "gemini",
        "label": "Gemini-Compatível (Google AI Proxy)",
        "protocol": "gemini-generate-content",
        "default_backend": "gemini",
        "default_url": "https://generativelanguage.googleapis.com",
    },
]


class _ProvidersProperty:
    def __get__(self, instance, owner):
        if instance is None:
            return owner.default_providers
        custom_names = [
            cp["name"]
            for cp in instance.custom_providers
            if cp["name"] not in instance.default_providers
        ]
        ordered: list[str] = []
        for provider in instance.favorite_providers:
            if provider not in ordered:
                ordered.append(provider)
        for provider in instance.default_providers:
            if provider not in ordered:
                ordered.append(provider)
        for provider in custom_names:
            if provider not in ordered:
                ordered.append(provider)
        return tuple(ordered)


class ProviderSelectionBehavior:
    """Provider catalog/favorites/custom CRUD and provider-popup navigation."""

    default_providers = (
        "kitt-reverse-proxy", "ollama", "lmstudio", "openai", "anthropic", "gemini",
        "deepseek", "groq", "together", "mistral", "openrouter", "xai", "fireworks",
        "cohere", "azure", "antigravity",
    )
    providers = _ProvidersProperty()

    @property
    def selected_pattern(self) -> dict:
        idx = max(0, min(self.pattern_index, len(PROVIDER_PATTERNS) - 1))
        return PROVIDER_PATTERNS[idx]


    def cycle_pattern(self, delta: int = 1) -> dict:
        self.pattern_index = (self.pattern_index + delta) % len(PROVIDER_PATTERNS)
        return self.selected_pattern


    def set_pattern_by_id(self, pattern_id: str) -> bool:
        pid = (pattern_id or "").strip().lower()
        for idx, pat in enumerate(PROVIDER_PATTERNS):
            if pat["id"] == pid or pid in pat["label"].lower():
                self.pattern_index = idx
                return True
        return False


    @property
    def selected_provider(self) -> str:
        provs = self.providers
        if self.provider_index >= len(provs):
            self.provider_index = 0
        return provs[self.provider_index]


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


    def toggle_favorite_provider(self, provider: str) -> bool:
        return self.toggle_favorite(provider)


    def add_custom_provider(
        self,
        name: str,
        base_url: str,
        backend: str = "openai",
        protocol: Optional[str] = None,
        api_key: str = "",
    ) -> None:
        name = name.strip().lower()
        if not name:
            return
        if not protocol:
            if "ollama" in backend.lower() or ":11434" in base_url or "ollama" in name:
                protocol = "ollama-chat"
                backend = "ollama"
            elif "anthropic" in backend.lower() or "claude" in name:
                protocol = "anthropic-messages"
                backend = "anthropic"
            elif "gemini" in backend.lower() or "google" in name:
                protocol = "gemini-generate-content"
                backend = "gemini"
            else:
                protocol = "openai-chat-completions"
                backend = "openai"

        self.custom_providers = [cp for cp in self.custom_providers if cp["name"] != name]
        self.custom_providers.append({
            "name": name,
            "base_url": base_url.strip(),
            "backend": backend,
            "protocol": protocol,
            "api_key": api_key.strip(),
        })
        if name not in self.favorite_providers:
            self.favorite_providers.append(name)
        provs = self.providers
        if name in provs:
            self.provider_index = provs.index(name)


    def get_custom_provider(self, name: str) -> dict | None:
        name_clean = (name or "").strip().lower()
        return next((cp for cp in self.custom_providers if cp.get("name", "").lower() == name_clean), None)


    def edit_custom_provider(
        self,
        name: str,
        new_name: str | None = None,
        base_url: str | None = None,
        backend: str | None = None,
        protocol: str | None = None,
        api_key: str | None = None,
    ) -> bool:
        entry = self.get_custom_provider(name)
        if not entry:
            return False
        old_name = entry["name"]
        target_name = (new_name or old_name).strip().lower()
        
        self.custom_providers = [cp for cp in self.custom_providers if cp.get("name", "").lower() != old_name.lower()]
        updated = {
            "name": target_name,
            "base_url": (base_url if base_url is not None else entry.get("base_url", "")).strip(),
            "backend": backend or entry.get("backend", "openai"),
            "protocol": protocol or entry.get("protocol", "openai-chat-completions"),
            "api_key": (api_key if api_key is not None else entry.get("api_key", "")).strip(),
        }
        self.custom_providers.append(updated)
        
        # Update favorites if renamed
        if old_name in self.favorite_providers:
            idx = self.favorite_providers.index(old_name)
            self.favorite_providers[idx] = target_name
        elif target_name not in self.favorite_providers:
            self.favorite_providers.append(target_name)

        provs = self.providers
        if target_name in provs:
            self.provider_index = provs.index(target_name)
        return True


    def delete_custom_provider(self, name: str) -> bool:
        clean_name = (name or "").strip().lower()
        before_len = len(self.custom_providers)
        self.custom_providers = [cp for cp in self.custom_providers if cp.get("name", "").lower() != clean_name]
        if clean_name in self.favorite_providers:
            self.favorite_providers = [f for f in self.favorite_providers if f.lower() != clean_name]
        self.provider_index = 0
        return len(self.custom_providers) < before_len


    def get_popup_entries(self) -> list[dict]:
        entries = []
        entries.append({"kind": "header", "title": "⭐ PROVEDORES FAVORITOS", "id": ""})
        for f in self.favorite_providers:
            entries.append({"kind": "provider", "name": f, "is_favorite": True, "id": f})

        # Local providers
        local_provs = [p for p in ("ollama", "lmstudio") if p not in self.favorite_providers and p in self.providers]
        if local_provs:
            entries.append({"kind": "header", "title": "💻 PROVEDORES LOCAIS", "id": ""})
            for lp in local_provs:
                entries.append({"kind": "provider", "name": lp, "is_favorite": False, "id": lp})

        # Cloud providers
        cloud_provs = [
            p for p in self.providers
            if p not in self.favorite_providers and p not in ("ollama", "lmstudio")
            and not any(cp["name"] == p for cp in self.custom_providers)
        ]
        if cloud_provs:
            entries.append({"kind": "header", "title": "🌐 PROVEDORES CLOUD", "id": ""})
            for cp in cloud_provs:
                entries.append({"kind": "provider", "name": cp, "is_favorite": False, "id": cp})

        # Custom providers
        customs = [cp["name"] for cp in self.custom_providers if cp["name"] not in self.favorite_providers]
        if customs:
            entries.append({"kind": "header", "title": "⚙ PROVEDORES CUSTOMIZADOS", "id": ""})
            for c in customs:
                entries.append({"kind": "provider", "name": c, "is_favorite": False, "id": c})

        # Actions for managing custom providers if any exist
        if self.custom_providers:
            entries.append({"kind": "header", "title": "🛠 GERENCIAR PROVEDORES CUSTOMIZADOS", "id": ""})
            for cp in self.custom_providers:
                cname = cp["name"]
                entries.append({
                    "kind": "action",
                    "name": f"edit_provider_{cname}",
                    "title": f"[✏ Editar: {cname} ({cp.get('base_url', '')})]",
                    "id": f"edit_provider_{cname}",
                    "target_provider": cname,
                })
                entries.append({
                    "kind": "action",
                    "name": f"delete_provider_{cname}",
                    "title": f"[🗑 Excluir: {cname}]",
                    "id": f"delete_provider_{cname}",
                    "target_provider": cname,
                })

        entries.append({"kind": "header", "title": "➕ ADICIONAR PROVEDOR (TEMPLATES & PADRÕES)", "id": ""})
        entries.append({
            "kind": "action",
            "name": "add_provider_ollama",
            "title": "[+ Novo Provedor Ollama (Servidor Local/Remoto - /api/tags)]",
            "id": "add_provider_ollama",
        })
        entries.append({
            "kind": "action",
            "name": "add_provider_openai",
            "title": "[+ Novo Provedor OpenAI-Compatível (vLLM / LM Studio / LocalAI)]",
            "id": "add_provider_openai",
        })
        entries.append({
            "kind": "action",
            "name": "add_provider",
            "title": "[+ Novo Provedor Customizado (Selecionar Padrão / Protocolo)]",
            "id": "add_provider",
        })
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


    def handle_popup_mouse_hover(self, visual_row: int) -> None:
        entries = self.get_popup_entries()
        total = len(entries)
        window_size = 25
        start = min(max(0, self.provider_popup_index - (window_size // 2)), max(0, total - window_size))
        offset = 1 if start > 0 else 0
        idx = start + max(0, visual_row - offset)
        if 0 <= idx < total and entries[idx]["kind"] in ("provider", "action"):
            self.provider_popup_index = idx

