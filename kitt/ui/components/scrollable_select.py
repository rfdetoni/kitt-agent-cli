"""Generic ScrollableSelect component with fuzzy filtering, categorization, keyboard and mouse support."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Generic, List, Optional, Sequence, Tuple, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class SelectOption(Generic[T]):
    title: str
    value: T
    description: Optional[str] = None
    category: Optional[str] = None
    badge: Optional[str] = None
    disabled: bool = False
    footer: Optional[str] = None
    details: Tuple[str, ...] = ()
    gutter: Optional[str] = None


class ScrollableSelect(Generic[T]):
    """Reusable selection controller with fuzzy filtering, sliding viewport, keyboard and mouse coexistence."""

    def __init__(
        self,
        options: Optional[Sequence[SelectOption[T]]] = None,
        viewport_size: int = 14,
        on_select: Optional[Callable[[SelectOption[T]], None]] = None,
    ):
        self._options: List[SelectOption[T]] = list(options or [])
        self.viewport_size = viewport_size
        self.on_select = on_select
        self.search_query: str = ""
        self.selected_index: int = 0
        self.input_mode: str = "keyboard"  # "keyboard" | "mouse"
        self._filtered_cache: Optional[List[SelectOption[T]]] = None
        self._row_to_index_map: List[Optional[int]] = []

    @property
    def options(self) -> List[SelectOption[T]]:
        return self._options

    @options.setter
    def options(self, new_options: Sequence[SelectOption[T]]) -> None:
        curr_selected = self.get_selected()
        curr_val = curr_selected.value if curr_selected else None
        self._options = list(new_options)
        self._filtered_cache = None
        if curr_val is not None:
            self.select_by_value(curr_val)
        else:
            self._clamp_index()

    def set_search_query(self, query: str) -> None:
        if self.search_query != query:
            self.search_query = query
            self._filtered_cache = None
            self.selected_index = 0

    def select_by_value(self, value: T) -> bool:
        filtered = self.get_filtered_options()
        for idx, opt in enumerate(filtered):
            if opt.value == value:
                self.selected_index = idx
                return True
        self._clamp_index()
        return False

    def select_by_title(self, title: str) -> bool:
        filtered = self.get_filtered_options()
        for idx, opt in enumerate(filtered):
            if opt.title.lower() == title.lower():
                self.selected_index = idx
                return True
        self._clamp_index()
        return False

    def get_filtered_options(self) -> List[SelectOption[T]]:
        if self._filtered_cache is not None:
            return self._filtered_cache

        q = self.search_query.strip().lower()
        if not q:
            self._filtered_cache = self._options
            return self._filtered_cache

        tokens = q.split()
        scored: List[Tuple[int, SelectOption[T]]] = []
        for opt in self._options:
            title_lower = opt.title.lower()
            cat_lower = (opt.category or "").lower()
            desc_lower = (opt.description or "").lower()
            details_lower = " ".join(opt.details).lower()
            val_lower = str(opt.value).lower()
            searchable = f"{title_lower} {cat_lower} {desc_lower} {details_lower} {val_lower}"
            if all(tok in searchable for tok in tokens):
                # Score higher for title prefix/exact match
                score = 0
                if title_lower == q:
                    score += 100
                elif title_lower.startswith(q):
                    score += 50
                elif any(tok in title_lower for tok in tokens):
                    score += 20
                if cat_lower and any(tok in cat_lower for tok in tokens):
                    score += 10
                scored.append((score, opt))

        scored.sort(key=lambda item: item[0], reverse=True)
        self._filtered_cache = [item[1] for item in scored]
        return self._filtered_cache

    def _clamp_index(self) -> None:
        filtered = self.get_filtered_options()
        if not filtered:
            self.selected_index = 0
        elif self.selected_index >= len(filtered):
            self.selected_index = len(filtered) - 1
        elif self.selected_index < 0:
            self.selected_index = 0

    def get_selected(self) -> Optional[SelectOption[T]]:
        filtered = self.get_filtered_options()
        if filtered and 0 <= self.selected_index < len(filtered):
            return filtered[self.selected_index]
        return None

    # --- Navigation ---

    def move(self, delta: int) -> None:
        self.input_mode = "keyboard"
        filtered = self.get_filtered_options()
        if filtered:
            self.selected_index = (self.selected_index + delta) % len(filtered)

    def page(self, delta: int) -> None:
        self.move(delta * max(1, self.viewport_size - 2))

    def home(self) -> None:
        self.input_mode = "keyboard"
        self.selected_index = 0

    def end(self) -> None:
        self.input_mode = "keyboard"
        filtered = self.get_filtered_options()
        if filtered:
            self.selected_index = len(filtered) - 1

    # --- Mouse Support ---

    def on_mouse_move(self, visual_row_offset: int) -> None:
        """Handles deliberate mouse hover, switching input_mode to mouse."""
        self.input_mode = "mouse"
        # Ensure row map is updated
        self.render_lines()
        if 0 <= visual_row_offset < len(self._row_to_index_map):
            target_idx = self._row_to_index_map[visual_row_offset]
            if target_idx is not None:
                filtered = self.get_filtered_options()
                if 0 <= target_idx < len(filtered):
                    self.selected_index = target_idx
                    return
        # Fallback to simple viewport indexing if map is empty
        filtered = self.get_filtered_options()
        if not filtered:
            return
        start, end = self.get_viewport_bounds()
        target_idx = start + visual_row_offset
        if start <= target_idx < end and target_idx < len(filtered):
            self.selected_index = target_idx

    def on_mouse_click(self, visual_row_offset: int) -> Optional[SelectOption[T]]:
        """Handles mouse click confirmation."""
        self.on_mouse_move(visual_row_offset)
        selected = self.get_selected()
        if selected and not selected.disabled and self.on_select:
            self.on_select(selected)
        return selected

    def on_mouse_wheel(self, delta: int) -> None:
        """Handles mouse scroll wheel."""
        self.move(delta)

    # --- Viewport & Rendering ---

    def get_viewport_bounds(self) -> Tuple[int, int]:
        filtered = self.get_filtered_options()
        total = len(filtered)
        if total <= self.viewport_size:
            return 0, total
        start = min(max(0, self.selected_index - (self.viewport_size // 2)), total - self.viewport_size)
        end = min(total, start + self.viewport_size)
        return start, end

    def render_lines(self) -> List[str]:
        filtered = self.get_filtered_options()
        total = len(filtered)
        self._row_to_index_map = []

        if not filtered:
            if self.search_query.strip():
                line = f"  Nenhum resultado encontrado para '{self.search_query}'."
            else:
                line = "  (Nenhuma opção disponível)"
            self._row_to_index_map.append(None)
            return [line]

        start, end = self.get_viewport_bounds()
        lines: List[str] = []

        if start > 0:
            lines.append(f"  ▲ ... ({start} itens acima)")
            self._row_to_index_map.append(None)

        last_category = None
        for idx in range(start, end):
            opt = filtered[idx]
            # Optional category separator when not filtering
            if not self.search_query.strip() and opt.category and opt.category != last_category:
                lines.append(f"  [{opt.category.upper()}]")
                self._row_to_index_map.append(None)
                last_category = opt.category

            marker = ">" if idx == self.selected_index else " "
            gutter = f"{opt.gutter} " if opt.gutter else ""
            badge = f" {opt.badge}" if opt.badge else ""
            disabled_str = " (desativado)" if opt.disabled else ""
            lines.append(f"{marker} [{idx+1}/{total}] {gutter}{opt.title:<32}{badge}{disabled_str}")
            self._row_to_index_map.append(idx)

        if end < total:
            lines.append(f"  ▼ ... ({total - end} itens abaixo)")
            self._row_to_index_map.append(None)

        return lines
