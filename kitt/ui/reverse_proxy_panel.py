from __future__ import annotations

import shlex
from dataclasses import dataclass, field

from kitt.reverse_proxy.bindings import bind_instance_to_role, normalize_role
from kitt.reverse_proxy.client import ReverseProxyClient, ReverseProxyControlError
from kitt.reverse_proxy.contracts import ReverseProxyInstance, ReverseProxyPlugin, ReverseProxyProfile


@dataclass
class ReverseProxyPanelModel:
    page: str = "instances"
    selected_index: int = 0
    profile_index: int = 0
    instances: list[ReverseProxyInstance] = field(default_factory=list)
    profiles: list[ReverseProxyProfile] = field(default_factory=list)
    plugins: list[ReverseProxyPlugin] = field(default_factory=list)
    error: str = ""
    loading: bool = False

    def items(self):
        if self.page == "plugins":
            return self.plugins
        if self.page == "profiles":
            return self.profiles
        return self.instances

    def move(self, delta: int) -> None:
        items = self.items()
        if not items:
            self.selected_index = 0
            return
        self.selected_index = (self.selected_index + delta) % len(items)

    def selected_instance(self) -> ReverseProxyInstance | None:
        if self.page != "instances" or not self.instances:
            return None
        return self.instances[min(self.selected_index, len(self.instances) - 1)]

    def selected_plugin(self) -> ReverseProxyPlugin | None:
        if self.page != "plugins" or not self.plugins:
            return None
        return self.plugins[min(self.selected_index, len(self.plugins) - 1)]

    def selected_profile(self) -> ReverseProxyProfile | None:
        if not self.profiles:
            return None
        index = self.selected_index if self.page == "profiles" else self.profile_index
        return self.profiles[min(index, len(self.profiles) - 1)]

    def cycle_profile(self, delta: int) -> None:
        if self.profiles:
            self.profile_index = (self.profile_index + delta) % len(self.profiles)

    def show(self, page: str) -> None:
        self.page = page
        self.selected_index = 0


async def _refresh_reverse_proxy(ui) -> None:
    model = ui.reverse_proxy_model
    model.loading = True
    model.error = ""
    try:
        instances, profiles, plugins = await ui._run_blocking(_load_snapshot, ui.reverse_proxy_client)
        model.instances = instances
        model.profiles = profiles
        model.plugins = plugins
        model.selected_index = min(model.selected_index, max(0, len(model.items()) - 1))
        model.profile_index = min(model.profile_index, max(0, len(model.profiles) - 1))
    except ReverseProxyControlError as error:
        model.error = str(error)
    finally:
        model.loading = False
        if ui.application:
            ui.application.invalidate()


def _load_snapshot(client: ReverseProxyClient):
    return client.list_instances(), client.list_profiles(), client.list_plugins()


async def _open_reverse_proxy_overlay(ui) -> None:
    model = ui.reverse_proxy_model
    model.show("instances")
    model.loading = True
    model.error = ""
    # Open first so the command is visibly responsive while the control-plane
    # subprocesses load instances, profiles and plugins.
    ui.open_overlay("reverse_proxy", ui.reverse_proxy_control)
    if ui.application:
        ui.application.invalidate()
    await _refresh_reverse_proxy(ui)


def _reverse_proxy_move(ui, delta: int) -> None:
    ui.reverse_proxy_model.move(delta)
    if ui.application:
        ui.application.invalidate()


def _reverse_proxy_cycle_profile(ui, delta: int) -> None:
    ui.reverse_proxy_model.cycle_profile(delta)
    if ui.application:
        ui.application.invalidate()


def _reverse_proxy_show(ui, page: str) -> None:
    ui.reverse_proxy_model.show(page)
    if ui.application:
        ui.application.invalidate()


def _prefill_prompt(ui, value: str) -> None:
    ui.close_overlay()
    ui.prompt_buffer.text = value
    ui.prompt_buffer.cursor_position = len(value)
    if ui.application:
        try:
            ui.application.layout.focus(ui.prompt_control)
        except Exception:
            pass
        ui.application.invalidate()


def _reverse_proxy_prepare_url(ui) -> None:
    _prefill_prompt(ui, "/reverse-proxy start https://")


def _reverse_proxy_prepare_profile_create(ui) -> None:
    _prefill_prompt(ui, "/reverse-proxy profile create ")


async def _reverse_proxy_start_selected(ui) -> None:
    plugin = ui.reverse_proxy_model.selected_plugin()
    if not plugin:
        ui.state.add_toast("Selecione um plugin para iniciar o serviço.", persistent=True)
        return
    profile = ui.reverse_proxy_model.selected_profile()
    ui.state.add_toast(f"Iniciando Reverse Proxy: {plugin.name}...")
    if ui.application:
        ui.application.invalidate()
    try:
        instance = await ui._run_blocking(
            ui.reverse_proxy_client.start_instance,
            plugin.id,
            profile=profile.id if profile else None,
        )
        ui.state.add_toast(f"Reverse Proxy iniciado: {instance.id} @ {instance.endpoint}")
        await _refresh_reverse_proxy(ui)
        ui.reverse_proxy_model.show("instances")
    except ReverseProxyControlError as error:
        ui.state.add_toast(f"Reverse Proxy: {error}", persistent=True)


async def _reverse_proxy_stop_selected(ui) -> None:
    instance = ui.reverse_proxy_model.selected_instance()
    if not instance:
        return
    try:
        await ui._run_blocking(ui.reverse_proxy_client.stop_instance, instance.id)
        ui.state.add_toast(f"Reverse Proxy parado: {instance.id}")
        await _refresh_reverse_proxy(ui)
    except ReverseProxyControlError as error:
        ui.state.add_toast(f"Reverse Proxy: {error}", persistent=True)


async def _reverse_proxy_restart_selected(ui) -> None:
    instance = ui.reverse_proxy_model.selected_instance()
    if not instance:
        return
    try:
        restarted = await ui._run_blocking(ui.reverse_proxy_client.restart_instance, instance.id)
        ui.state.add_toast(f"Reverse Proxy reiniciado: {restarted.id}")
        await _refresh_reverse_proxy(ui)
    except ReverseProxyControlError as error:
        ui.state.add_toast(f"Reverse Proxy: {error}", persistent=True)


async def _reverse_proxy_remove_profile(ui) -> None:
    profile = ui.reverse_proxy_model.selected_profile()
    if not profile:
        return
    try:
        removed = await ui._run_blocking(ui.reverse_proxy_client.remove_profile, profile.id)
        ui.state.add_toast(
            f"Perfil removido do registro: {profile.id}" if removed else f"Perfil não encontrado: {profile.id}"
        )
        await _refresh_reverse_proxy(ui)
    except ReverseProxyControlError as error:
        ui.state.add_toast(f"Reverse Proxy: {error}", persistent=True)


async def _reverse_proxy_bind_selected(ui, role: str) -> None:
    instance = ui.reverse_proxy_model.selected_instance()
    if not instance:
        return
    await bind_instance_to_role(ui, role, instance)
    ui.state.add_toast(f"{normalize_role(role).title()} → {instance.provider}/{instance.model}")


def _hovered_button_text(ui, text: str, action: str, value=None) -> str:
    hovered = ui.interactions.hovered("reverse_proxy")
    if hovered and hovered.action == action and repr(hovered.value) == repr(value):
        return text.upper()
    return text


def _register_button(ui, row: int, line: str, text: str, action: str, value=None) -> None:
    visible = _hovered_button_text(ui, text, action, value)
    ui.interactions.add_text("reverse_proxy", row, line, visible, action, value)


def _buttons_line(ui, buttons: list[tuple[str, str, object | None]]) -> str:
    return "  ".join(
        _hovered_button_text(ui, text, action, value)
        for text, action, value in buttons
    )


def _reverse_proxy_text(ui) -> str:
    model = ui.reverse_proxy_model
    ui.interactions.begin("reverse_proxy")
    if model.loading:
        return "KITT Reverse Proxy\n\n  Carregando serviços, perfis e plugins..."
    if model.error:
        text = (
            "KITT Reverse Proxy\n\n"
            f"  ⚠ {model.error}\n\n"
            "  Atualize o kitt-reverse-proxy para uma versão com control plane v1.\n"
            "  [F5] Tentar novamente  [Esc] Fechar"
        )
        lines = text.splitlines()
        _register_button(
            ui, len(lines) - 1, lines[-1], "[F5] Tentar novamente", "reverse_proxy.refresh"
        )
        return text

    if model.page == "plugins":
        profile = model.selected_profile()
        lines = [
            "NOVO SERVIÇO — escolha um plugin de conexão",
            "",
            f"Perfil: {profile.name if profile else 'automático por provider'}  [Tab] alternar",
            "",
        ]
        for index, plugin in enumerate(model.plugins):
            marker = ">" if index == model.selected_index else " "
            lines.append(
                f"{marker} [{index + 1}/{len(model.plugins)}] "
                f"{plugin.name:<20} {plugin.default_model:<20} {plugin.source}"
            )
            ui.interactions.add_row(
                "reverse_proxy", len(lines) - 1, "reverse_proxy.item", index
            )
        plugin_buttons = [
            ("[Enter] Iniciar serviço", "reverse_proxy.start", None),
            ("[u] Informar URL", "reverse_proxy.url", None),
            ("[p] Perfis", "reverse_proxy.show", "profiles"),
            ("[i] Serviços", "reverse_proxy.show", "instances"),
        ]
        lines.extend([
            "",
            _buttons_line(ui, plugin_buttons),
        ])
        row = len(lines) - 1
        line = lines[row]
        _register_button(ui, row, line, "[Enter] Iniciar serviço", "reverse_proxy.start")
        _register_button(ui, row, line, "[u] Informar URL", "reverse_proxy.url")
        _register_button(ui, row, line, "[p] Perfis", "reverse_proxy.show", "profiles")
        _register_button(ui, row, line, "[i] Serviços", "reverse_proxy.show", "instances")
        profile_line = lines[2]
        _register_button(
            ui, 2, profile_line, "[Tab] alternar", "reverse_proxy.profile_next"
        )
        return "\n".join(lines)

    if model.page == "profiles":
        lines = ["PERFIS DE NAVEGADOR", ""]
        if not model.profiles:
            lines.append("  Nenhum perfil registrado.")
        for index, profile in enumerate(model.profiles):
            marker = ">" if index == model.selected_index else " "
            providers = ", ".join(profile.providers) or "sem provider associado"
            legacy = " · legado" if profile.legacy else ""
            lines.append(
                f"{marker} [{index + 1}/{len(model.profiles)}] "
                f"{profile.name:<24} {providers}{legacy}"
            )
            ui.interactions.add_row(
                "reverse_proxy", len(lines) - 1, "reverse_proxy.item", index
            )
        profile_buttons = [
            ("[a] Criar perfil", "reverse_proxy.profile_create", None),
            ("[d] Remover registro", "reverse_proxy.profile_remove", None),
            ("[n] Iniciar serviço", "reverse_proxy.show", "plugins"),
            ("[i] Serviços", "reverse_proxy.show", "instances"),
        ]
        lines.extend([
            "",
            _buttons_line(ui, profile_buttons),
            "Dados Chromium só são apagados quando solicitado diretamente ao reverse-proxy.",
        ])
        row = len(lines) - 2
        line = lines[row]
        _register_button(ui, row, line, "[a] Criar perfil", "reverse_proxy.profile_create")
        _register_button(ui, row, line, "[d] Remover registro", "reverse_proxy.profile_remove")
        _register_button(ui, row, line, "[n] Iniciar serviço", "reverse_proxy.show", "plugins")
        _register_button(ui, row, line, "[i] Serviços", "reverse_proxy.show", "instances")
        return "\n".join(lines)

    router = ui.runtime.processor.router
    _, context = router.resolve_profile_for_task("context-gather")
    _, code = router.resolve_profile_for_task("code-generation")
    lines = [
        "KITT REVERSE PROXY — Multi-instância",
        "",
        "ROTEAMENTO DO AGENT",
        f"  Contexto     {context.backend}/{context.model} @ {context.base_url}",
        f"  Codificação  {code.backend}/{code.model} @ {code.base_url}",
        "",
        "SERVIÇOS",
    ]
    if not model.instances:
        lines.append("  Nenhum serviço ativo.")
    for index, instance in enumerate(model.instances):
        marker = ">" if index == model.selected_index else " "
        lines.append(
            f"{marker} [{index + 1}/{len(model.instances)}] "
            f"{instance.id:<20} {instance.provider:<10} "
            f"{instance.status:<9} {instance.endpoint} · perfil {instance.profile_id}"
        )
        ui.interactions.add_row(
            "reverse_proxy", len(lines) - 1, "reverse_proxy.item", index
        )
    service_buttons = [
        ("[n] Novo serviço", "reverse_proxy.show", "plugins"),
        ("[p] Perfis", "reverse_proxy.show", "profiles"),
        ("[F5] Atualizar", "reverse_proxy.refresh", None),
        ("[r] Reiniciar", "reverse_proxy.restart", None),
        ("[x] Parar", "reverse_proxy.stop", None),
    ]
    role_buttons = [
        ("[c] Usar em Contexto", "reverse_proxy.bind", "context"),
        ("[e] Usar em Código", "reverse_proxy.bind", "principal"),
        ("[v] Usar em Validação", "reverse_proxy.bind", "validation"),
    ]
    lines.extend([
        "",
        _buttons_line(ui, service_buttons),
        _buttons_line(ui, role_buttons),
        "[←/→] outros painéis  [Esc] Fechar",
    ])
    actions_row = len(lines) - 3
    role_row = len(lines) - 2
    actions_line = lines[actions_row]
    role_line = lines[role_row]
    _register_button(ui, actions_row, actions_line, "[n] Novo serviço", "reverse_proxy.show", "plugins")
    _register_button(ui, actions_row, actions_line, "[p] Perfis", "reverse_proxy.show", "profiles")
    _register_button(ui, actions_row, actions_line, "[F5] Atualizar", "reverse_proxy.refresh")
    _register_button(ui, actions_row, actions_line, "[r] Reiniciar", "reverse_proxy.restart")
    _register_button(ui, actions_row, actions_line, "[x] Parar", "reverse_proxy.stop")
    _register_button(ui, role_row, role_line, "[c] Usar em Contexto", "reverse_proxy.bind", "context")
    _register_button(ui, role_row, role_line, "[e] Usar em Código", "reverse_proxy.bind", "principal")
    _register_button(ui, role_row, role_line, "[v] Usar em Validação", "reverse_proxy.bind", "validation")
    return "\n".join(lines)


def _flag_value(tokens: list[str], name: str) -> str | None:
    try:
        index = tokens.index(name)
    except ValueError:
        return None
    return tokens[index + 1] if index + 1 < len(tokens) else None


async def handle_reverse_proxy_command(ui, argument: str) -> None:
    if not argument.strip() or argument.strip() in {"status", "list"}:
        await _open_reverse_proxy_overlay(ui)
        return

    try:
        tokens = shlex.split(argument)
    except ValueError as error:
        ui._show_result(f"Invalid reverse-proxy command: {error}")
        return
    if not tokens:
        await _open_reverse_proxy_overlay(ui)
        return

    action = tokens[0].lower()
    try:
        if action == "start":
            if len(tokens) < 2:
                raise ValueError("Usage: /reverse-proxy start <plugin|url> [--profile NAME] [--id ID] [--role ROLE]")
            raw_port = _flag_value(tokens, "--port")
            instance = await ui._run_blocking(
                ui.reverse_proxy_client.start_instance,
                tokens[1],
                profile=_flag_value(tokens, "--profile"),
                instance_id=_flag_value(tokens, "--id"),
                port=int(raw_port) if raw_port else None,
            )
            role = _flag_value(tokens, "--role")
            if role:
                await bind_instance_to_role(ui, role, instance)
            ui._show_result(
                f"Reverse Proxy iniciado\n{instance.id}: {instance.provider}/{instance.model}\n{instance.endpoint}"
                + (f"\nRole: {normalize_role(role)}" if role else "")
            )
            return

        if action == "stop":
            target = tokens[1] if len(tokens) > 1 else ""
            if target == "all":
                count = await ui._run_blocking(ui.reverse_proxy_client.stop_all)
                ui._show_result(f"{count} serviço(s) do KITT Reverse Proxy parado(s).")
            elif target:
                stopped = await ui._run_blocking(ui.reverse_proxy_client.stop_instance, target)
                ui._show_result(f"Serviço {target} parado." if stopped else f"Serviço não encontrado: {target}")
            else:
                raise ValueError("Usage: /reverse-proxy stop <instance|all>")
            return

        if action == "restart":
            if len(tokens) < 2:
                raise ValueError("Usage: /reverse-proxy restart <instance>")
            instance = await ui._run_blocking(ui.reverse_proxy_client.restart_instance, tokens[1])
            ui._show_result(f"Serviço reiniciado: {instance.id} @ {instance.endpoint}")
            return

        if action == "bind":
            if len(tokens) < 3:
                raise ValueError("Usage: /reverse-proxy bind <context|code|validation> <instance>")
            instances = await ui._run_blocking(ui.reverse_proxy_client.list_instances)
            instance = next((item for item in instances if item.id == tokens[2]), None)
            if not instance:
                raise ValueError(f"Unknown reverse-proxy instance: {tokens[2]}")
            await bind_instance_to_role(ui, tokens[1], instance)
            ui._show_result(f"{normalize_role(tokens[1]).title()} → {instance.id} ({instance.provider}/{instance.model})")
            return

        if action in {"plugins", "plugin"}:
            plugins = await ui._run_blocking(ui.reverse_proxy_client.list_plugins)
            ui._show_result("\n".join(f"{item.id}: {item.name} ({item.default_model})" for item in plugins))
            return

        if action in {"profile", "profiles"}:
            sub = tokens[1].lower() if len(tokens) > 1 else "list"
            if sub == "create":
                if len(tokens) < 3:
                    raise ValueError("Usage: /reverse-proxy profile create <name> [provider]")
                provider = tokens[3] if len(tokens) > 3 else None
                profile = await ui._run_blocking(ui.reverse_proxy_client.create_profile, tokens[2], provider)
                ui._show_result(f"Perfil criado: {profile.id}")
                return
            if sub == "remove":
                if len(tokens) < 3:
                    raise ValueError("Usage: /reverse-proxy profile remove <id>")
                removed = await ui._run_blocking(ui.reverse_proxy_client.remove_profile, tokens[2])
                ui._show_result(f"Perfil removido: {tokens[2]}" if removed else f"Perfil não encontrado: {tokens[2]}")
                return
            profiles = await ui._run_blocking(ui.reverse_proxy_client.list_profiles)
            ui._show_result("\n".join(
                f"{item.id}: {', '.join(item.providers) or 'sem provider'}" for item in profiles
            ) or "Nenhum perfil registrado.")
            return

        raise ValueError(
            "Usage: /reverse-proxy [start|stop|restart|bind|plugins|profile]"
        )
    except (ReverseProxyControlError, ValueError) as error:
        ui._show_result(f"KITT Reverse Proxy: {error}")
