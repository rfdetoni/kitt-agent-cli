from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class HitRegion:
    """One local-cell interaction target inside a retained TUI surface."""

    surface: str
    y: int
    x_start: int
    x_end: int
    action: str
    value: Any = None

    def contains(self, x: int, y: int) -> bool:
        return self.y == y and self.x_start <= x < self.x_end

    @property
    def key(self) -> tuple[str, str, str]:
        return self.surface, self.action, repr(self.value)


class InteractionMap:
    """Tiny per-surface hit grid inspired by OpenTUI's rendered-cell hit testing.

    Regions are rebuilt by render functions from the same text the user sees.
    Business actions remain in controllers; this object only maps local cells
    to semantic action ids and tracks hover/press state.
    """

    _ROW_END = 1_000_000

    def __init__(self) -> None:
        self._regions: dict[str, list[HitRegion]] = {}
        self._hovered: dict[str, HitRegion] = {}
        self._pressed: dict[str, HitRegion] = {}

    def begin(self, surface: str) -> None:
        self._regions[surface] = []
        self._hovered.pop(surface, None)
        self._pressed.pop(surface, None)

    def add_row(self, surface: str, y: int, action: str, value: Any = None) -> HitRegion:
        return self.add(surface, y, 0, self._ROW_END, action, value)

    def add_text(
        self,
        surface: str,
        y: int,
        line: str,
        text: str,
        action: str,
        value: Any = None,
    ) -> HitRegion | None:
        start = line.find(text)
        if start < 0:
            return None
        return self.add(surface, y, start, start + len(text), action, value)

    def add(
        self,
        surface: str,
        y: int,
        x_start: int,
        x_end: int,
        action: str,
        value: Any = None,
    ) -> HitRegion:
        region = HitRegion(
            surface=surface,
            y=max(0, int(y)),
            x_start=max(0, int(x_start)),
            x_end=max(int(x_start) + 1, int(x_end)),
            action=action,
            value=value,
        )
        self._regions.setdefault(surface, []).append(region)
        return region

    def resolve(self, surface: str, x: int, y: int) -> HitRegion | None:
        for region in reversed(self._regions.get(surface, ())):
            if region.contains(int(x), int(y)):
                return region
        return None

    def hover(self, surface: str, x: int, y: int) -> HitRegion | None:
        region = self.resolve(surface, x, y)
        if region is None:
            self._hovered.pop(surface, None)
        else:
            self._hovered[surface] = region
        return region

    def press(self, surface: str, x: int, y: int) -> HitRegion | None:
        region = self.hover(surface, x, y)
        if region is None:
            self._pressed.pop(surface, None)
        else:
            self._pressed[surface] = region
        return region

    def release(self, surface: str, x: int, y: int) -> HitRegion | None:
        region = self.hover(surface, x, y)
        pressed = self._pressed.pop(surface, None)
        if pressed is None:
            return region
        if region is not None and region.key == pressed.key:
            return region
        return None

    def hovered(self, surface: str) -> HitRegion | None:
        return self._hovered.get(surface)
