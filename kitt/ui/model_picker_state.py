from __future__ import annotations


class ModelSelectionBehavior:
    """Model/role selection, filtering and model metadata presentation."""

    roles = ("principal", "context", "validation")

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

