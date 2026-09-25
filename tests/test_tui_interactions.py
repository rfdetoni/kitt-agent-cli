from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from prompt_toolkit.data_structures import Point
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType

from kitt.ui.interaction import InteractionMap
from kitt.ui.mouse import interactive_surface_mouse_handler
from kitt.ui.reverse_proxy_panel import ReverseProxyPanelModel
from kitt.reverse_proxy.contracts import ReverseProxyProfile


def _mouse(event_type: MouseEventType, x: int, y: int) -> MouseEvent:
    return MouseEvent(
        position=Point(x=x, y=y),
        event_type=event_type,
        button=MouseButton.LEFT,
        modifiers=frozenset(),
    )


def test_interaction_map_uses_local_cell_bounds_and_latest_region_wins():
    hit_map = InteractionMap()
    hit_map.begin("surface")
    hit_map.add_row("surface", 2, "row", 1)
    hit_map.add("surface", 2, 4, 10, "button", "ok")

    assert hit_map.resolve("surface", 1, 2).action == "row"
    assert hit_map.resolve("surface", 6, 2).action == "button"
    assert hit_map.resolve("surface", 6, 3) is None


def test_mouse_release_only_activates_same_pressed_region():
    hit_map = InteractionMap()
    hit_map.begin("surface")
    hit_map.add("surface", 1, 0, 5, "left")
    hit_map.add("surface", 1, 6, 10, "right")

    hit_map.press("surface", 2, 1)
    assert hit_map.release("surface", 8, 1) is None

    hit_map.press("surface", 2, 1)
    assert hit_map.release("surface", 2, 1).action == "left"


def test_palette_mouse_path_matches_keyboard_selection_and_activation():
    async def scenario():
        ui = SimpleNamespace(
            interactions=InteractionMap(),
            palette_index=0,
            application=MagicMock(),
            _run_selected_palette=AsyncMock(),
        )
        ui.application.layout.focus = MagicMock()
        ui.palette_control = object()
        ui.interactions.begin("palette")
        ui.interactions.add_row("palette", 3, "palette.item", 4)

        interactive_surface_mouse_handler(ui, "palette", _mouse(MouseEventType.MOUSE_MOVE, 2, 3))
        assert ui.palette_index == 4

        interactive_surface_mouse_handler(ui, "palette", _mouse(MouseEventType.MOUSE_DOWN, 2, 3))
        interactive_surface_mouse_handler(ui, "palette", _mouse(MouseEventType.MOUSE_UP, 2, 3))
        await asyncio.sleep(0)
        ui._run_selected_palette.assert_awaited_once()

    asyncio.run(scenario())


def test_profile_page_uses_visible_selection_instead_of_stale_cycle_index():
    model = ReverseProxyPanelModel(page="profiles", selected_index=1, profile_index=0)
    model.profiles = [
        ReverseProxyProfile(id="first", name="First", providers=(), legacy=False),
        ReverseProxyProfile(id="second", name="Second", providers=(), legacy=False),
    ]

    assert model.selected_profile().id == "second"


def test_pressed_target_survives_rerender_but_must_match_new_hit_target():
    hit_map = InteractionMap()
    hit_map.begin("surface")
    hit_map.add("surface", 1, 0, 5, "open", "profile")
    hit_map.press("surface", 2, 1)

    # A render pass rebuilds coordinates but preserves the semantic press.
    hit_map.begin("surface")
    hit_map.add("surface", 2, 10, 20, "open", "profile")
    assert hit_map.release("surface", 12, 2).action == "open"

    # A moved pointer over another semantic target must not activate the old press.
    hit_map.begin("surface")
    hit_map.add("surface", 2, 10, 20, "left")
    hit_map.add("surface", 2, 21, 30, "right")
    hit_map.press("surface", 12, 2)
    hit_map.begin("surface")
    hit_map.add("surface", 3, 10, 20, "left")
    hit_map.add("surface", 3, 21, 30, "right")
    assert hit_map.release("surface", 22, 3) is None
