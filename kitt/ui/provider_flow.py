from __future__ import annotations

import asyncio
import json
import os
import time

from kitt.ui.theme import DEFAULT_THEME

def _select_popup_action(ui, entry: dict) -> None:
    ui.close_overlay()
    aname = entry.get("name", "")
    if aname == "add_provider_ollama":
        ui._open_add_provider_overlay("ollama")
    elif aname == "add_provider_openai":
        ui._open_add_provider_overlay("openai")
    elif aname.startswith("edit_provider_"):
        target = entry.get("target_provider", aname.replace("edit_provider_", ""))
        ui._open_edit_provider_overlay(target)
    elif aname.startswith("delete_provider_"):
        target = entry.get("target_provider", aname.replace("delete_provider_", ""))
        asyncio.create_task(ui._delete_custom_provider(target))
    else:
        ui._open_add_provider_overlay()


def _open_provider_popup_overlay(ui) -> None:
    ui.open_overlay("provider_popup", ui.provider_popup_control)


def _provider_popup_text(ui) -> str:
    setup = ui.model_setup_model
    entries = setup.get_popup_entries()
    total = len(entries)
    lines = [
        f"Menu de Provedores ({total} opções)  (Espaço/F: Favorito ★ | A/+: Novo | E: Editar | D: Excluir | Enter: Selecionar | Esc: Fechar)\n"
    ]
    window_size = 25
    start = min(max(0, setup.provider_popup_index - (window_size // 2)), max(0, total - window_size))
    end = min(total, start + window_size)

    if start > 0:
        lines.append(f"  ▲ ... ({start} opções acima)\n")

    from kitt.llm.auth import ProviderAuthService
    auth_service = ProviderAuthService()

    for idx in range(start, end):
        entry = entries[idx]
        if entry["kind"] == "header":
            lines.append(f"\n {entry['title']}")
        elif entry["kind"] == "provider":
            cursor = ">" if idx == setup.provider_popup_index else " "
            star = "★" if entry["is_favorite"] else "☆"
            pname = entry["name"]
            is_current = " [ativo]" if pname == setup.selected_provider else ""
            if ui._is_local_or_no_auth_provider(pname):
                status_glyph = "◌ local / sem auth"
            elif bool(auth_service.resolve(None, pname)):
                status_glyph = "● conectado"
            else:
                status_glyph = "○ não autenticado"
            lines.append(f" {cursor} {star} {pname:<16} │ {status_glyph:<18}{is_current}")
        elif entry["kind"] == "action":
            cursor = ">" if idx == setup.provider_popup_index else " "
            lines.append(f" {cursor} {entry['title']}")

    if end < total:
        lines.append(f"\n  ▼ ... ({total - end} opções abaixo)")
    return "\n".join(lines)


async def _persist_custom_providers(ui) -> None:
    try:
        router = getattr(ui.runtime.processor, "router", None)
        if router and hasattr(router, "config") and router.config:
            router.config.custom_providers = list(ui.model_setup_model.custom_providers)
            if hasattr(ui.runtime.processor, "registry"):
                for cp in ui.model_setup_model.custom_providers:
                    ui.runtime.processor.registry.register_custom_provider(
                        provider_id=cp.get("name", ""),
                        name=cp.get("name", ""),
                        protocol=cp.get("protocol", "openai-chat-completions"),
                        base_url=cp.get("base_url", ""),
                    )
            await ui._run_blocking(router.save_config, ui.state.workspace_path)
            if await ui._ensure_daemon_management():
                await ui.bridge.reload_router()
    except Exception as err:
        ui.state.add_toast(f"Aviso: falha ao persistir provedores: {err}")


def _open_add_provider_overlay(ui, preset: str | None = None) -> None:
    ui.editing_provider_name = None
    if preset:
        ui.model_setup_model.set_pattern_by_id(preset)
    pat = ui.model_setup_model.selected_pattern
    ui.add_provider_name_buffer.text = ""
    ui.add_provider_url_buffer.text = pat.get("default_url", "http://")
    ui.open_overlay("add_provider", ui.add_provider_name_control)


def _open_edit_provider_overlay(ui, name: str) -> None:
    ui.editing_provider_name = name
    cp = ui.model_setup_model.get_custom_provider(name)
    if not cp:
        ui.state.add_toast(f"Provedor customizado '{name}' não encontrado para edição.", persistent=True)
        return

    proto = cp.get("protocol", "openai-chat-completions")
    bkend = cp.get("backend", "openai")
    if "ollama" in proto or "ollama" in bkend:
        ui.model_setup_model.set_pattern_by_id("ollama")
    elif "anthropic" in proto or "anthropic" in bkend:
        ui.model_setup_model.set_pattern_by_id("anthropic")
    elif "gemini" in proto or "gemini" in bkend:
        ui.model_setup_model.set_pattern_by_id("gemini")
    else:
        ui.model_setup_model.set_pattern_by_id("openai")

    ui.add_provider_name_buffer.text = cp.get("name", name)
    ui.add_provider_url_buffer.text = cp.get("base_url", "http://")
    ui.open_overlay("add_provider", ui.add_provider_url_control)


async def _delete_custom_provider(ui, name: str) -> None:
    deleted = ui.model_setup_model.delete_custom_provider(name)
    if deleted:
        await ui._persist_custom_providers()
        ui.state.add_toast(f"✓ Provedor '{name}' removido e configuração atualizada!", persistent=False)
        await ui._prepare_model_setup()
    else:
        ui.state.add_toast(f"Provedor '{name}' não encontrado para exclusão.", persistent=True)
    if ui.application:
        ui.application.invalidate()


def _add_provider_help_text(ui) -> str:
    pat = ui.model_setup_model.selected_pattern
    header = f"Editar Provedor Customizado '{ui.editing_provider_name}'" if ui.editing_provider_name else "Cadastrar Novo Provedor Customizado"
    return (
        f"{header}\n"
        f"Padrão / Protocolo: ◄ {pat['label']} ►  ([P] ou [Ctrl+←/→] para alterar padrão)\n"
        f"[Tab] Alternar Nome/URL  |  [Enter] Salvar e Descobrir  |  [Esc] Cancelar\n"
    )


def _accept_add_provider(ui, buffer) -> bool:
    name = ui.add_provider_name_buffer.text.strip().lower()
    url = buffer.text.strip().rstrip("/")
    if not name:
        ui.state.add_toast("Nome do provedor não pode ser vazio.", persistent=True)
        return False
    from kitt.llm.endpoint_security import is_reserved_provider_id
    if is_reserved_provider_id(name):
        ui.state.add_toast(
            f"'{name}' é um ID reservado de provedor built-in. Use um nome customizado único.",
            persistent=True,
        )
        return False
    if not url.startswith(("http://", "https://")):
        url = f"http://{url}"
    pat = ui.model_setup_model.selected_pattern

    if ui.editing_provider_name:
        ui.model_setup_model.edit_custom_provider(
            name=ui.editing_provider_name,
            new_name=name,
            base_url=url,
            backend=pat.get("default_backend", "openai"),
            protocol=pat.get("protocol", "openai-chat-completions"),
        )
    else:
        ui.model_setup_model.add_custom_provider(
            name=name,
            base_url=url,
            backend=pat.get("default_backend", "openai"),
            protocol=pat.get("protocol", "openai-chat-completions"),
        )
    ui.close_overlay()
    asyncio.get_running_loop().create_task(ui._finish_add_provider(name, url))
    return True


async def _finish_add_provider(ui, name: str, url: str) -> None:
    from kitt.llm.endpoint_security import ProviderEndpointTrustStore
    ProviderEndpointTrustStore().trust(name, url)
    await ui._persist_custom_providers()
    await ui._prepare_model_setup(base_url=url, provider=name)
    action_msg = "atualizado" if ui.editing_provider_name else "adicionado"
    ui.state.add_toast(f"✓ Provedor '{name}' {action_msg} e persistido com sucesso!", persistent=False)
    ui.editing_provider_name = None
    if ui.application:
        ui.application.invalidate()


def _open_provider_endpoint_overlay(ui) -> None:
    profile = ui._profile_for_role(ui.model_setup_model.selected_role)
    ui.provider_endpoint_buffer.text = ui.model_setup_model.base_url_override or (profile.base_url if profile and profile.backend == "ollama" else "http://")
    ui.open_overlay("provider_endpoint", ui.provider_endpoint_control)


async def _submit_provider_endpoint(ui, endpoint: str) -> None:
    endpoint = endpoint.strip().rstrip("/")
    if not endpoint.startswith(("http://", "https://")):
        ui.state.add_toast("Endpoint must start with http:// or https://", persistent=True)
        return
    from kitt.llm.endpoint_security import ProviderEndpointTrustStore
    ProviderEndpointTrustStore().trust(
        ui.model_setup_model.selected_provider,
        endpoint,
    )
    ui.close_overlay()
    await ui._prepare_model_setup(endpoint)
    if ui.application:
        ui.application.invalidate()


def _auth_login_help_text(ui) -> str:
    from kitt.llm.auth import ProviderAuthService
    prov = ui.target_auth_provider or "Provedor"
    env_var = ProviderAuthService.get_default_env_var(prov)
    env_val = ProviderAuthService.get_env_value(env_var)

    env_status = ""
    if env_val:
        masked = env_val[:4] + "..." + env_val[-3:] if len(env_val) > 8 else "***"
        env_status = (
            f"  ✓ Variável ${env_var} detectada no ambiente / .env ({masked})\n"
            f"  ★ Pressione [Enter com campo vazio] ou digite 'env' para usar ${env_var}\n"
        )
    else:
        env_status = (
            f"  • Variável de ambiente: export {env_var}=\"sua_chave\"\n"
            f"    (ou adicione '{env_var}=sua_chave' no arquivo .env do projeto)\n"
        )

    return (
        f"Autenticação do Provedor: {prov.upper()}\n\n"
        f"{env_status}\n"
        f"Ou digite a API Key / Secret abaixo (Salva em ~/.kitt/auth.json [0600]):\n"
        "[Enter] Salvar Credencial  |  [Esc] Pular/Cancelar\n"
    )


async def _start_oauth_flow(ui, provider: str) -> None:
    from kitt.llm.auth import ProviderAuthService
    from kitt.llm.oauth import OAuthManager
    mgr = OAuthManager()
    if not mgr.is_oauth_supported(provider):
        ui.state.add_toast(f"OAuth não suportado para {provider}. Digite a API key.", persistent=False)
        return

    cfg = mgr.get_config(provider)
    if not cfg:
        return

    auth_service = ProviderAuthService()

    if cfg.flow_type == "device_code":
        try:
            challenge = await ui._run_blocking(mgr.start_device_code_flow, provider)
            ui.state.add_toast(
                f"🔑 Acesse {challenge.verification_uri} e digite o código: {challenge.user_code}",
                persistent=True,
            )
            try:
                import webbrowser
                webbrowser.open(challenge.verification_uri)
            except Exception:
                pass
            token = await ui._run_blocking(mgr.poll_device_code_token, provider, challenge, 180.0)
            auth_service.login_oauth(provider, token)
            ui.state.add_toast(f"✓ Conectado via OAuth com sucesso ({provider})!", persistent=False)
            ui.close_overlay()
            if ui.pending_model_selection:
                role, model, prov, base_url = ui.pending_model_selection
                ui.pending_model_selection = None
                await ui._apply_pending_model(role, model, prov, base_url)
        except Exception as exc:
            ui.state.add_toast(f"OAuth falhou: {exc}", persistent=True)
    else:
        try:
            auth_url, server, verifier, state = await ui._run_blocking(mgr.start_browser_flow, provider, True)
            ui.state.add_toast(f"⏳ Navegador aberto. Ou acesse: {auth_url}", persistent=True)

            res = await ui._run_blocking(server.wait_for_callback, 120.0)
            server.stop()
            received_state = res.get("state", "")
            from kitt.llm.oauth import validate_state
            if not validate_state(state, received_state):
                ui.state.add_toast("Erro de segurança OAuth: Validação de state falhou (CSRF).", persistent=True)
                return

            code = res.get("code")
            if not code:
                ui.state.add_toast("Código de autorização não recebido", persistent=True)
                return

            redirect_uri = f"http://127.0.0.1:{server.port}/callback"
            token = await ui._run_blocking(mgr.exchange_code_for_token, provider, code, verifier, redirect_uri)
            auth_service.login_oauth(provider, token)
            ui.state.add_toast(f"✓ Conectado via OAuth com sucesso ({provider})!", persistent=False)
            ui.close_overlay()
            if ui.pending_model_selection:
                role, model, prov, base_url = ui.pending_model_selection
                ui.pending_model_selection = None
                await ui._apply_pending_model(role, model, prov, base_url)
        except Exception as exc:
            ui.state.add_toast(f"OAuth falhou: {exc}", persistent=True)
    if ui.application:
        ui.application.invalidate()


def _accept_model_setup_search(ui, buffer) -> bool:
    asyncio.create_task(ui._apply_selected_model())
    return True


def _open_auth_login_overlay(ui, provider: str, parent_name: str | None = None) -> None:
    ui.target_auth_provider = provider.strip().lower()
    ui.auth_login_buffer.text = ""
    ui.open_overlay("auth_login", ui.auth_login_control, parent_name=parent_name)


def _accept_auth_login(ui, buffer) -> bool:
    key = buffer.text.strip()
    prov = ui.target_auth_provider or "openai"
    from kitt.llm.auth import ProviderAuthService
    auth_service = ProviderAuthService()
    env_var = auth_service.get_default_env_var(prov)
    env_val = auth_service.get_env_value(env_var)

    def _trust_pending_endpoint() -> None:
        from kitt.llm.endpoint_security import ProviderEndpointTrustStore
        endpoint = None
        if ui.pending_model_selection:
            _, _, pending_provider, pending_url = ui.pending_model_selection
            if pending_provider == prov:
                endpoint = pending_url
        if not endpoint and hasattr(ui, "model_setup_model"):
            custom = next(
                (
                    cp for cp in ui.model_setup_model.custom_providers
                    if cp.get("name") == prov
                ),
                None,
            )
            if custom:
                endpoint = custom.get("base_url")
        if endpoint:
            ProviderEndpointTrustStore().trust(prov, endpoint)

    if key.lower() in ("e", "env", "use_env", "$env"):
        if env_val:
            _trust_pending_endpoint()
            auth_service.login(prov, f"env:{env_var}", method="env")
            ui.state.add_toast(f"✓ Conectado via variável de ambiente (${env_var})!", persistent=False)
            ui.close_overlay()
            if ui.pending_model_selection:
                role, model, provider, base_url = ui.pending_model_selection
                ui.pending_model_selection = None
                asyncio.create_task(ui._apply_pending_model(role, model, provider, base_url))
            if ui.application:
                ui.application.invalidate()
            return True
        else:
            ui.state.add_toast(f"Variável ${env_var} não encontrada no ambiente ou .env. Digite a API Key.", persistent=True)
            return False

    if not key:
        if env_val:
            _trust_pending_endpoint()
            auth_service.login(prov, f"env:{env_var}", method="env")
            ui.state.add_toast(f"✓ Conectado via variável de ambiente (${env_var})!", persistent=False)
            ui.close_overlay()
            if ui.pending_model_selection:
                role, model, provider, base_url = ui.pending_model_selection
                ui.pending_model_selection = None
                asyncio.create_task(ui._apply_pending_model(role, model, provider, base_url))
            if ui.application:
                ui.application.invalidate()
            return True
        elif ui._is_local_or_no_auth_provider(prov):
            auth_service.login(prov, "", method="none")
            ui.state.add_toast(f"✓ Provedor '{prov}' configurado sem token.", persistent=False)
            ui.close_overlay()
            if ui.pending_model_selection:
                role, model, provider, base_url = ui.pending_model_selection
                ui.pending_model_selection = None
                asyncio.create_task(ui._apply_pending_model(role, model, provider, base_url))
            if ui.application:
                ui.application.invalidate()
            return True
        else:
            ui.state.add_toast(f"Autenticação de {prov} cancelada.", persistent=False)
            ui.pending_model_selection = None
            ui.close_overlay()
            return True

    _trust_pending_endpoint()
    auth_service.login(prov, key, method="api_key")
    ui.state.add_toast(f"✓ Credenciais salvas com segurança para {prov}!", persistent=False)
    ui.close_overlay()

    if ui.pending_model_selection:
        role, model, provider, base_url = ui.pending_model_selection
        ui.pending_model_selection = None
        asyncio.create_task(ui._apply_pending_model(role, model, provider, base_url))

    if ui.application:
        ui.application.invalidate()
    return True


async def _apply_pending_model(ui, role: str, model: str, provider: str, base_url: str | None) -> None:
    try:
        await ui._set_model_role(role, model, provider, base_url)
        ui.state.add_toast(f"✓ Cargo '{role.title()}' definido: {provider}/{model} (Esc para fechar)", duration=3.5)
    except Exception as exc:
        ui.state.add_toast(f"Falha ao atribuir modelo: {exc}", persistent=True)
    if ui.application:
        ui.application.invalidate()


def _is_local_or_no_auth_provider(ui, provider: str, url: str = "") -> bool:
    p = (provider or "").strip().lower()
    u = (url or "").strip().lower()
    if p in ("ollama", "lmstudio", "custom", "kitt-reverse-proxy", "kitt-proxy") or "ollama" in p or "lmstudio" in p or "kitt-reverse-proxy" in p:
        return True
    if hasattr(ui, "model_setup_model") and any(cp["name"] == p for cp in ui.model_setup_model.custom_providers):
        return True
    lan_markers = (
        ":11434", ":1234", ":3000", ":8000", ":8080", ":5000",
        "localhost", "127.0.0.1", "192.168.", "10.",
        "172.16.", "172.17.", "172.18.", "172.19.", "172.20.",
        "172.21.", "172.22.", "172.23.", "172.24.", "172.25.",
        "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31.",
        ".local", ".lan", ".internal"
    )
    if any(marker in u for marker in lan_markers):
        return True
    return False


async def _apply_selected_model(ui) -> None:
    model = ui.model_setup_model.selected_model
    if not model:
        ui.state.add_toast("Nenhum modelo selecionado", persistent=True)
        return
    role = ui.model_setup_model.selected_role
    provider = ui.model_setup_model.selected_provider
    profile = ui._profile_for_role(role)
    base_url = ui.model_setup_model.base_url_override or (profile.base_url if profile and profile.backend == provider else ui._provider_defaults(provider)[0])

    from kitt.llm.auth import ProviderAuthService
    auth_service = ProviderAuthService()
    auth_state = auth_service.state(provider)
    is_auth = (
        (auth_state.auth_type == "none")
        or bool(auth_service.resolve(auth_state.credential_ref, provider))
        or ui._is_local_or_no_auth_provider(provider, base_url)
    )

    if not is_auth:
        ui.pending_model_selection = (role, model, provider, base_url)
        ui._open_auth_login_overlay(provider, parent_name="model_setup")
        if ui.application:
            ui.application.invalidate()
        return

    try:
        await ui._set_model_role(role, model, provider, base_url)
        ui.state.add_toast(f"✓ Cargo '{role.title()}' definido: {provider}/{model} (Esc para fechar)", duration=3.5)
    except Exception as exc:
        ui.state.add_toast(f"Falha ao atualizar modelo: {exc}", persistent=True)
    if ui.application:
        ui.application.invalidate()

