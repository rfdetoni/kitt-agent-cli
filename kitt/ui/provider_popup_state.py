from __future__ import annotations


class ProviderPopupBehavior:
    """Provider-popup projection navigation and mouse row mapping."""

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


