from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from prompt_toolkit.data_structures import Point
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType

from kitt.ui.interaction import InteractionMap
from kitt.ui.mouse import interactive_surface_mouse_handler
from kitt.ui.render.core import _toast_text
from kitt.ui.render.overlays import _permission_text
from kitt.ui.state import UIState
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


def test_click_does_not_virtualize_target_on_mouse_down():
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

        interactive_surface_mouse_handler(
            ui, "palette", _mouse(MouseEventType.MOUSE_DOWN, 2, 3)
        )
        assert ui.palette_index == 0

        interactive_surface_mouse_handler(
            ui, "palette", _mouse(MouseEventType.MOUSE_UP, 2, 3)
        )
        await asyncio.sleep(0)
        assert ui.palette_index == 4
        ui._run_selected_palette.assert_awaited_once()


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


def test_model_header_click_uses_existing_role_controller():
    async def scenario():
        model = SimpleNamespace(role_index=0, selected_role="principal", selected_provider="openai")
        ui = SimpleNamespace(
            interactions=InteractionMap(),
            model_setup_model=model,
            application=MagicMock(),
            _move_model_role=AsyncMock(),
        )
        ui.application.layout.focus = MagicMock()
        ui.model_setup_search_control = object()
        ui.interactions.begin("model_setup_header")
        ui.interactions.add_row("model_setup_header", 2, "model.role", 1)

        interactive_surface_mouse_handler(
            ui, "model_setup_header", _mouse(MouseEventType.MOUSE_DOWN, 4, 2)
        )
        interactive_surface_mouse_handler(
            ui, "model_setup_header", _mouse(MouseEventType.MOUSE_UP, 4, 2)
        )
        await asyncio.sleep(0)
        ui._move_model_role.assert_awaited_once_with(1)

    asyncio.run(scenario())


def test_permission_renderer_only_marks_visible_action_text_clickable():
    state = UIState()
    state.pending_approvals = [{
        "tool_name": "run_command",
        "args": {"argv": ["python", "-V"]},
        "approval_id": "approval-1",
    }]
    ui = SimpleNamespace(
        state=state,
        interactions=InteractionMap(),
        approval_menu_index=0,
    )

    text = _permission_text(ui)
    lines = text.splitlines()
    row = next(i for i, line in enumerate(lines) if "[y] Permitir uma vez" in line)
    marker_start = lines[row].index("[y] Permitir uma vez")

    assert ui.interactions.resolve("permission", marker_start + 1, row) is not None
    assert ui.interactions.resolve("permission", 0, row) is None


def test_permission_blank_click_is_consumed_without_resolving_or_closing():
    async def scenario():
        ui = SimpleNamespace(
            interactions=InteractionMap(),
            approval_menu_index=0,
            application=MagicMock(),
            permission_control=object(),
            resolve_approval=AsyncMock(),
        )
        ui.application.layout.focus = MagicMock()
        ui.interactions.begin("permission")
        ui.interactions.add(
            "permission", 5, 10, 30, "permission.action", (0, "once")
        )

        down = interactive_surface_mouse_handler(
            ui, "permission", _mouse(MouseEventType.MOUSE_DOWN, 1, 5)
        )
        up = interactive_surface_mouse_handler(
            ui, "permission", _mouse(MouseEventType.MOUSE_UP, 1, 5)
        )
        await asyncio.sleep(0)

        assert down is None
        assert up is None
        ui.application.layout.focus.assert_called()
        ui.resolve_approval.assert_not_awaited()

    asyncio.run(scenario())


def test_permission_action_requires_press_and_release_inside_exact_label():
    async def scenario():
        ui = SimpleNamespace(
            interactions=InteractionMap(),
            approval_menu_index=0,
            application=MagicMock(),
            permission_control=object(),
            resolve_approval=AsyncMock(),
        )
        ui.application.layout.focus = MagicMock()
        ui.interactions.begin("permission")
        ui.interactions.add(
            "permission", 5, 10, 30, "permission.action", (0, "once")
        )

        interactive_surface_mouse_handler(
            ui, "permission", _mouse(MouseEventType.MOUSE_DOWN, 12, 5)
        )
        interactive_surface_mouse_handler(
            ui, "permission", _mouse(MouseEventType.MOUSE_UP, 12, 5)
        )
        await asyncio.sleep(0)

        ui.resolve_approval.assert_awaited_once_with("once")

    asyncio.run(scenario())


def test_recoverable_model_failure_renders_clickable_continue_and_retry_actions():
    state = UIState()
    state.pending_recovery = {
        "turn_id": "turn-1",
        "conversation_id": "conv-1",
        "error": "agent_contract_invalid",
        "recovery_action": "continue",
    }
    ui = SimpleNamespace(
        state=state,
        interactions=InteractionMap(),
        prompt_buffer=SimpleNamespace(text=""),
    )

    text = _toast_text(ui)
    lines = text.splitlines()
    assert "[c] Continuar" in lines[1]
    assert "[r] Tentar novamente" in lines[1]

    continue_x = lines[1].index("[c] Continuar") + 1
    retry_x = lines[1].index("[r] Tentar novamente") + 1
    assert ui.interactions.resolve("recovery", continue_x, 1).value == "continue"
    assert ui.interactions.resolve("recovery", retry_x, 1).value == "retry"


def test_recovery_mouse_action_calls_same_recovery_controller():
    async def scenario():
        ui = SimpleNamespace(
            interactions=InteractionMap(),
            application=MagicMock(),
            toast_control=object(),
            _recover_model_turn=AsyncMock(),
        )
        ui.application.layout.focus = MagicMock()
        ui.interactions.begin("recovery")
        ui.interactions.add(
            "recovery", 1, 2, 14, "recovery.action", "continue"
        )

        interactive_surface_mouse_handler(
            ui, "recovery", _mouse(MouseEventType.MOUSE_DOWN, 4, 1)
        )
        interactive_surface_mouse_handler(
            ui, "recovery", _mouse(MouseEventType.MOUSE_UP, 4, 1)
        )
        await asyncio.sleep(0)

        ui._recover_model_turn.assert_awaited_once_with("continue")

    asyncio.run(scenario())
