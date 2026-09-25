from __future__ import annotations


def toggle_turn_mode(ui, target_mode: str | None = None) -> str:
    modes = ["code", "plan", "ask"]
    if target_mode and target_mode.lower() in modes:
        ui.state.turn_mode = target_mode.lower()
    else:
        curr_idx = modes.index(ui.state.turn_mode) if ui.state.turn_mode in modes else 0
        ui.state.turn_mode = modes[(curr_idx + 1) % len(modes)]

    ui.state.planning_mode = (ui.state.turn_mode == "plan")
    mode_descs = {
        "code": "Modo [CODE] ativo: edição e execução de ferramentas habilitadas",
        "plan": "Modo [PLAN] ativo: análise e planejamento (sem escrita de código)",
        "ask": "Modo [ASK] ativo: pergunta e dúvidas (sem chamadas de ferramentas)",
    }
    ui.state.add_toast(mode_descs.get(ui.state.turn_mode, f"Modo: {ui.state.turn_mode.upper()}"), persistent=False)
    if ui.application:
        ui.application.invalidate()
    return ui.state.turn_mode

_model_setup_mouse_handler = _mouse.model_setup_mouse_handler
_provider_popup_mouse_handler = _mouse.provider_popup_mouse_handler
_select_popup_action = _provider_flow._select_popup_action
_open_provider_popup_overlay = _provider_flow._open_provider_popup_overlay
_provider_popup_text = _provider_flow._provider_popup_text
_persist_custom_providers = _provider_flow._persist_custom_providers
_open_add_provider_overlay = _provider_flow._open_add_provider_overlay
_open_edit_provider_overlay = _provider_flow._open_edit_provider_overlay
_delete_custom_provider = _provider_flow._delete_custom_provider
_add_provider_help_text = _provider_flow._add_provider_help_text
_accept_add_provider = _provider_flow._accept_add_provider
_finish_add_provider = _provider_flow._finish_add_provider
_open_provider_endpoint_overlay = _provider_flow._open_provider_endpoint_overlay

async def _open_session_picker_overlay(ui) -> None:
    await ui.session_picker_model.reload()
    ui.open_overlay("session_picker", ui.session_picker_control)


async def _open_timeline_overlay(ui) -> None:
    await ui.timeline_model.reload(ui.state.active_conversation_id)
    ui.open_overlay("timeline", ui.timeline_control)


async def _open_diff_overlay(ui) -> None:
    await ui.diff_model.reload()
    ui.open_overlay("diff", ui.diff_control)


def _toggle_sidebar(ui):
    ui.state.sidebar_open = not ui.state.sidebar_open
    if ui.application: ui.application.invalidate()


def _palette_changed(ui):
    ui.palette_index = 0
    if ui.application: ui.application.invalidate()


def _move_palette(ui, amount: int) -> None:
    matches = ui.commands.search(ui.palette_buffer.text)
    ui.palette_index = (ui.palette_index + amount) % max(1, len(matches))
    if ui.application: ui.application.invalidate()


async def _move_model_role(ui, amount: int) -> None:
    ui.model_setup_model.move_role(amount)
    ui.model_setup_model.base_url_override = None
    profile = ui._profile_for_role(ui.model_setup_model.selected_role)
    if profile and profile.backend in ui.model_setup_model.providers:
        ui.model_setup_model.provider_index = ui.model_setup_model.providers.index(profile.backend)
    provider = ui.model_setup_model.selected_provider
    base_url = profile.base_url if profile and profile.backend == provider else ui._provider_defaults(provider)[0]
    ui.model_setup_model.models = await ui._models_for_provider(provider, base_url)
    selected = ui._model_for_role(ui.model_setup_model.selected_role)
    if selected in ui.model_setup_model.models:
        ui.model_setup_model.model_index = ui.model_setup_model.models.index(selected)
    else:
        ui.model_setup_model.model_index = 0
    if ui.application:
        ui.application.invalidate()


async def _move_model_provider(ui, amount: int) -> None:
    ui.model_setup_model.move_provider(amount)
    ui.model_setup_model.base_url_override = None
    provider = ui.model_setup_model.selected_provider
    base_url, _ = ui._provider_defaults(provider)
    ui.model_setup_model.models = await ui._models_for_provider(provider, base_url)
    selected = ui._model_for_role(ui.model_setup_model.selected_role)
    if selected in ui.model_setup_model.models:
        ui.model_setup_model.model_index = ui.model_setup_model.models.index(selected)
    else:
        ui.model_setup_model.model_index = 0
    if ui.application:
        ui.application.invalidate()


def _new_conversation(ui) -> None:
    conversation = ui.runtime.history.new_conversation()
    ui.state.active_conversation_id = conversation["id"]
    ui.state.route = "home"
    ui.state.transcript.clear()
    ui.explicit_files.clear()
    ui.prompt_buffer.reset()
    if ui.application: ui.application.invalidate()


async def _run_selected_palette(ui):
    matches = ui.commands.search(ui.palette_buffer.text)
    if matches:
        ui.close_overlay()
        await ui._execute_command(matches[ui.palette_index].aliases[0])

