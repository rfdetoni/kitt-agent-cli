from __future__ import annotations

from kitt.ui.theme import DEFAULT_THEME

def _agents_text(ui) -> str:
    from kitt.ui.components.agents_dashboard import AgentsDashboardComponent
    return AgentsDashboardComponent().render(ui.state, max(40, ui.state.width - 16))


def _live_agents_text(ui) -> str:
    tasks = ui.state.active_tasks
    if not tasks:
        return ""
    running = [tk for tk in tasks if tk.status == "running"]
    if running:
        items = []
        for tk in running:
            glyph = "●"
            tag = "CHILD" if tk.kind == "child_agent" else "CORE"
            step = ui.state.scanner_step + tk.scanner_phase
            scan = DEFAULT_THEME.scanner_frame(step, 8)
            items.append(f"{glyph} [{tag}:{tk.name[:14]}] [{scan}] {tk.progress}%")
        return " " + " | ".join(items) + "  (Ctrl+X A for dashboard)"
    else:
        done = [tk for tk in tasks if tk.status == "done"]
        err = [tk for tk in tasks if tk.status == "error"]
        if ui.state.status_text.startswith("✔") or "COMPLETED" in ui.state.status_text:
            recovered = f" | {len(err)} tentativa(s) recuperada(s)" if err else ""
            return f" ✔ [PROCESSO CONCLUÍDO] {len(done)} tarefa(s)/agente(s) finalizados com sucesso{recovered}!"
        if err:
            return f" ✖ [FALHA NO PROCESSO] {len(err)} tarefa(s) com erro | {len(done)} concluída(s)"
        return f" ✔ [PROCESSO CONCLUÍDO] {len(done)} tarefa(s)/agente(s) finalizados com sucesso!"


def _permission_text(ui):
    from kitt.ui.components.permission_card import PermissionCardComponent

    text = PermissionCardComponent().render(
        ui.state, max(50, ui.state.width - 10), ui.approval_menu_index
    )
    ui.interactions.begin("permission")
    lines = text.splitlines()
    for index, (action, label, shortcut) in enumerate(PermissionCardComponent.ACTIONS):
        marker = f"[{shortcut}] {label}"
        for row, line in enumerate(lines):
            if marker in line:
                ui.interactions.add_row(
                    "permission", row, "permission.action", (index, action)
                )
                break
    return text


def _autonomy_text(ui) -> str:
    t = DEFAULT_THEME
    curr = ui.runtime.autonomy_store.get()
    command_mode = (
        "DENY" if curr.level == "read_only"
        else "ALLOW ALL" if curr.allow_run_command_auto
        else "ASK"
    )
    rules = getattr(ui.runtime.approval, "remembered_rules", [])
    rules_str = "\n".join(f"  • {r.tool_name} ({r.path_glob or '*'}) -> {r.decision.upper()} [{r.scope}]" for r in rules[-5:]) if rules else "  (Nenhuma regra salva)"

    text = (
        t.format_primary("┌── CENTRAL DE PERMISSÕES & AUTONOMIA / AUTONOMY CONTROL ───────────────────┐\n") +
        f"│ Perfil Atual: [ {curr.level.upper()} ]  Comandos: [ {command_mode} ]\n" +
        "│\n" +
        "│ Política para comandos e alterações:\n" +
        "│  [1] ALLOW ALL : Executar automaticamente dentro das regras críticas\n" +
        "│  [2] ASK       : Pedir aprovação antes de comandos e alterações\n" +
        "│  [3] DENY      : Bloquear comandos, alterações e subagentes\n" +
        "│\n" +
        "│ Regras Salvas no Workspace:\n" +
        f"{rules_str}\n" +
        "│\n" +
        "│ Controles: [1] Allow All  [2] Ask  [3] Deny  [r] Limpar Regras  [Esc] Sair\n" +
        t.format_primary("└────────────────────────────────────────────────────────────────────────────┘")
    )
    ui.interactions.begin("autonomy")
    for row, line in enumerate(text.splitlines()):
        if "[1] ALLOW ALL" in line:
            ui.interactions.add_row("autonomy", row, "autonomy.profile", "autonomous")
        elif "[2] ASK" in line:
            ui.interactions.add_row("autonomy", row, "autonomy.profile", "supervised")
        elif "[3] DENY" in line:
            ui.interactions.add_row("autonomy", row, "autonomy.profile", "read_only")
        elif "Controles:" in line:
            ui.interactions.add_text(
                "autonomy", row, line, "[r] Limpar Regras", "autonomy.clear"
            )
    return text


def _palette_text(ui):
    from kitt.ui.components.command_palette import CommandPaletteComponent

    query = ui.palette_buffer.text
    commands = ui.commands.search(query)
    window_size = 10
    text = CommandPaletteComponent(ui.commands, ui.keymap).render(
        query=query,
        selected_index=ui.palette_index,
        width=max(40, ui.state.width - 16),
        window_size=window_size,
    )
    ui.interactions.begin("palette")
    if commands:
        total = len(commands)
        start = min(
            max(0, ui.palette_index - (window_size // 2)),
            max(0, total - window_size),
        )
        end = min(total, start + window_size)
        row = 1 if start > 0 else 0
        for index in range(start, end):
            ui.interactions.add_row("palette", row, "palette.item", index)
            ui.interactions.add_row("palette", row + 1, "palette.item", index)
            row += 2
    return text


def _session_picker_text(ui):
    sessions = ui.session_picker_model.sessions
    ui.interactions.begin("session_picker")
    if not sessions:
        q = ui.session_picker_model.query.strip()
        if q:
            return f"  Nenhuma conversa encontrada para '{q}'.\n  Limpe a busca ou tente outro termo."
        return "  Nenhuma conversa anterior encontrada.\n  Inicie uma nova conversa para salvar o histórico."
    total = len(sessions)
    lines = [f"Buscar Conversas ({total} salvas)  (Enter: Retomar  |  Esc: Voltar)\n"]
    window_size = 12
    start = min(max(0, ui.session_picker_model.selected_index - (window_size // 2)), max(0, total - window_size))
    end = min(total, start + window_size)

    if start > 0:
        lines.append(f"  ▲ ... ({start} conversas anteriores)")

    ui.interactions.begin("session_picker")
    for idx in range(start, end):
        s = sessions[idx]
        prefix = "> " if idx == ui.session_picker_model.selected_index else "  "
        lines.append(f"{prefix}[{idx+1}/{total}] {s.get('id', '')[:8]}  {s.get('title', 'Sem título')}")
        ui.interactions.add_row(
            "session_picker", len(lines) - 1, "session.item", idx
        )

    if end < total:
        lines.append(f"  ▼ ... ({total - end} conversas mais antigas)")
    return "\n".join(lines)


def _timeline_text(ui):
    turns = ui.timeline_model.turns
    ui.interactions.begin("timeline")
    if not turns:
        return "  Nenhum turno registrado na conversa ativa."
    total = len(turns)
    lines = [f"Linha do Tempo ({total} turnos)  (Esc: Voltar)\n"]
    window_size = 12
    start = min(max(0, ui.timeline_model.selected_index - (window_size // 2)), max(0, total - window_size))
    end = min(total, start + window_size)

    if start > 0:
        lines.append(f"  ▲ ... ({start} turnos anteriores)")

    for idx in range(start, end):
        t = turns[idx]
        prefix = "> " if idx == ui.timeline_model.selected_index else "  "
        lines.append(f"{prefix}[{idx+1}/{total}] Turno {t.get('ordinal', idx+1)} ({t.get('id', '')[:8]})")
        ui.interactions.add_row("timeline", len(lines) - 1, "timeline.item", idx)

    if end < total:
        lines.append(f"  ▼ ... ({total - end} turnos seguintes)")
    return "\n".join(lines)


def _diff_text(ui):
    diff = ui.diff_model.diff_text
    if not diff:
        return "Unified diff preview\n\nNo pending diff."
    lines = diff.splitlines()[ui.diff_model.scroll_offset:ui.diff_model.scroll_offset + 30]
    return "Unified diff preview (Use Up/Down to scroll)\n\n" + "\n".join(lines)


def _model_setup_header_text(ui) -> str:
    setup = ui.model_setup_model
    lines = [
        " [Tab] Alternar Cargo  |  [T] Limites Locais  |  [P / Espaço] Menu Provedores (★)  |  [L] Login/Auth  |  [Enter] Selecionar  |  [Esc] Fechar",
        " Atribuições de Modelos por Cargo:"
    ]
    for role in setup.roles:
        marker = ">" if role == setup.selected_role else " "
        profile = ui._profile_for_role(role)
        endpoint = profile.base_url if profile else "?"
        enforced = getattr(profile, "enforce_local_limits", True) if profile else True
        lim_badge = "[Lim: On]" if enforced else "[Lim: Off]"
        lines.append(f" {marker} {role.title():10} {(profile.backend if profile else '?')}/{ui._model_for_role(role)} @ {endpoint} {lim_badge}")
    
    profile = ui._profile_for_role(setup.selected_role)
    endpoint = setup.base_url_override or (profile.base_url if (profile and profile.backend == setup.selected_provider) else ui._provider_defaults(setup.selected_provider)[0])
    star = "★" if setup.selected_provider in setup.favorite_providers else "☆"

    from kitt.llm.auth import ProviderAuthService
    auth_service = ProviderAuthService()
    is_auth = bool(auth_service.resolve(None, setup.selected_provider))
    if ui._is_local_or_no_auth_provider(setup.selected_provider, endpoint):
        auth_badge = "[◌ Local / Sem Token Necessário]"
    elif is_auth:
        auth_badge = "[● Conectado / Autenticado]"
    else:
        auth_badge = "[○ Não autenticado — L: Conectar]"

    src_badge = f"(Origem: {setup.source})" if hasattr(setup, "source") and setup.source else ""
    lines.append(f" Provedor Selecionado: {star} {setup.selected_provider} @ {endpoint} {auth_badge} {src_badge}")
    return "\n".join(lines)


def _model_setup_text(ui):
    setup = ui.model_setup_model
    if getattr(setup, "loading", False):
        return "  ◌ Carregando lista de modelos do provedor..."

    if getattr(setup, "error_message", None):
        return (
            f"  ⚠ Não foi possível consultar modelos do provedor '{setup.selected_provider}'.\n"
            f"  Motivo: {setup.error_message}\n\n"
            "  [Enter] Tentar novamente  |  [E] Editar endpoint  |  [Esc] Voltar"
        )

    filtered = setup.get_filtered_models()
    total_models = len(filtered)
    all_models = len(setup.models)
    
    filter_tag = f" (Filtrando {total_models}/{all_models})" if setup.search_query.strip() else f" ({all_models} modelos)"
    lines = [f"Modelos Disponíveis{filter_tag}:"]
    
    if not filtered:
        if setup.search_query.strip():
            lines.append(f"\n  Nenhum modelo encontrado para o filtro '{setup.search_query}'.\n  Limpe o filtro de busca.")
        else:
            lines.append(f"\n  Nenhum modelo reportado pelo provedor '{setup.selected_provider}'.\n  [E] Configurar endpoint  |  [Esc] Voltar")
        return "\n".join(lines)

    window_size = 14
    start = min(max(0, setup.model_index - (window_size // 2)), max(0, total_models - window_size))
    end = min(total_models, start + window_size)

    if start > 0:
        lines.append(f"  ▲ ... ({start} modelos acima)")

    for index in range(start, end):
        model = filtered[index]
        marker = ">" if index == setup.model_index else " "
        badge = setup.format_model_badge(setup.selected_provider, model)
        lines.append(f"{marker} [{index+1}/{total_models}] {model:<34}{badge}")

    if end < total_models:
        lines.append(f"  ▼ ... ({total_models - end} modelos abaixo)")
    return "\n".join(lines)


def _help_text(ui):
    shortcuts = ["ATALHOS — Ctrl+P descobre todas as ações", "Mouse ativo por padrão: role sobre o painel desejado; /mouse alterna para seleção nativa.", ""]
    shortcuts.extend(f"{keys:22} {description}" for _, keys, description in ui.keymap.get_help_list())
    shortcuts.extend(["", "COMANDOS", ""])
    shortcuts.extend(f"{c.aliases[0]:22} {c.description}" for c in ui.commands.commands.values())
    return "\n".join(shortcuts)

