from __future__ import annotations

import inspect
import shlex
from typing import TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:
    from kitt.ui.app import KittUIApp


def parse_model_command(arg: str) -> Tuple[str, str, Optional[str], Optional[str]]:
    tokens = shlex.split(arg) if arg else []
    if not tokens:
        return "", "", None, None

    role = tokens[0].lower()
    valid_roles = {"principal", "context", "validation", "all"}
    if role not in valid_roles:
        return "all", tokens[0], None, tokens[1] if len(tokens) > 1 else None
    if len(tokens) == 1:
        return role, "", None, None
    if len(tokens) == 2:
        return role, tokens[1], None, None
    if len(tokens) == 3:
        return role, tokens[2], tokens[1], None
    return role, tokens[2], tokens[1], tokens[3]


async def handle_model_command(app: KittUIApp, arg: str) -> None:
    if not arg:
        await app._open_model_setup_overlay()
        return

    role, model, provider, base_url = parse_model_command(arg)
    if not model:
        app._show_result(
            "Usage: /model <principal|context|validation|all> [provider] <model> [base_url]"
        )
        return

    roles = app.model_setup_model.roles if role == "all" else (role,)
    if base_url:
        from kitt.llm.endpoint_security import ProviderEndpointTrustStore
        effective_provider = provider or app._profile_for_role(roles[0]).backend
        ProviderEndpointTrustStore().trust(effective_provider, base_url)
    for selected_role in roles:
        await app._set_model_role(selected_role, model, provider, base_url)
    backend = app._profile_for_role(roles[0]).backend
    app._show_result(f"{role.title()} model saved and active: {(provider or backend)}/{model}")


async def handle_setup_models_command(app: KittUIApp, arg: str) -> None:
    if arg and not arg.startswith(("http://", "https://")):
        app._show_result("Usage: /setup-models [http://server:11434]")
        return
    await app._open_model_setup_overlay(arg or None)


async def _await_if_needed(result) -> None:
    if inspect.isawaitable(result):
        await result


async def handle_add_provider_command(app: KittUIApp, arg: str) -> None:
    tokens = shlex.split(arg) if arg else []
    if not tokens:
        app._open_add_provider_overlay()
        return

    name = tokens[0].lower()
    pattern = tokens[1].lower() if len(tokens) > 1 else "ollama"
    url = tokens[2] if len(tokens) > 2 else ""

    if not url and pattern.startswith(
        ("http://", "https://", "192.", "10.", "172.", "127.", "localhost")
    ):
        url = pattern
        pattern = "ollama" if ("11434" in url or "ollama" in name) else "openai"

    if not url:
        app._show_result(
            "Uso: /add-provider <nome> [ollama|openai|anthropic|gemini] <url_base> [api_key]"
        )
        return

    if not url.startswith(("http://", "https://")):
        url = f"http://{url}"

    from kitt.llm.endpoint_security import (
        ProviderEndpointTrustStore,
        is_reserved_provider_id,
    )
    if is_reserved_provider_id(name):
        app._show_result(
            f"'{name}' é um ID built-in reservado. Use um nome customizado único."
        )
        return
    ProviderEndpointTrustStore().trust(name, url)

    app.model_setup_model.set_pattern_by_id(pattern)
    selected_pattern = app.model_setup_model.selected_pattern
    app.model_setup_model.add_custom_provider(
        name=name,
        base_url=url,
        backend=selected_pattern.get("default_backend", "openai"),
        protocol=selected_pattern.get("protocol", "openai-chat-completions"),
        api_key=tokens[3] if len(tokens) > 3 else "",
    )

    if hasattr(app, "_persist_custom_providers"):
        await _await_if_needed(app._persist_custom_providers())
    if hasattr(app, "_open_model_setup_overlay"):
        await _await_if_needed(app._open_model_setup_overlay(url, provider=name))

    app.state.add_toast(
        f"✓ Provedor '{name}' ({selected_pattern['label']}) adicionado com sucesso!",
        persistent=False,
    )


async def handle_edit_provider_command(app: KittUIApp, arg: str) -> None:
    tokens = shlex.split(arg) if arg else []
    if not tokens:
        app._show_result(
            "Uso: /edit-provider <nome> [nova_url] [padrao: ollama|openai|anthropic|gemini] [api_key]"
        )
        return

    name = tokens[0].lower()
    existing = app.model_setup_model.get_custom_provider(name)
    if not existing:
        app._show_result(f"Provedor customizado '{name}' não encontrado para edição.")
        return

    if len(tokens) == 1:
        app._open_edit_provider_overlay(name)
        return

    url = tokens[1] if len(tokens) > 1 else existing.get("base_url", "")
    if url and not url.startswith(("http://", "https://")):
        url = f"http://{url}"

    from kitt.llm.endpoint_security import (
        ProviderEndpointTrustStore,
        is_reserved_provider_id,
    )
    if is_reserved_provider_id(name):
        app._show_result(
            f"'{name}' é um ID built-in reservado e não pode ser editado como custom."
        )
        return
    ProviderEndpointTrustStore().trust(name, url)

    pattern = tokens[2].lower() if len(tokens) > 2 else existing.get("backend", "openai")
    app.model_setup_model.set_pattern_by_id(pattern)
    selected_pattern = app.model_setup_model.selected_pattern
    api_key = tokens[3] if len(tokens) > 3 else existing.get("api_key", "")

    app.model_setup_model.edit_custom_provider(
        name=name,
        base_url=url,
        backend=selected_pattern.get("default_backend", "openai"),
        protocol=selected_pattern.get("protocol", "openai-chat-completions"),
        api_key=api_key,
    )

    if hasattr(app, "_persist_custom_providers"):
        await _await_if_needed(app._persist_custom_providers())
    if hasattr(app, "_open_model_setup_overlay"):
        await _await_if_needed(app._open_model_setup_overlay(url, provider=name))

    app.state.add_toast(f"✓ Provedor '{name}' atualizado e persistido com sucesso!", persistent=False)


async def handle_delete_provider_command(app: KittUIApp, arg: str) -> None:
    name = arg.strip().lower() if arg else ""
    if not name:
        app._show_result("Uso: /delete-provider <nome>")
        return

    app.model_setup_model.delete_custom_provider(name)
    if hasattr(app, "_persist_custom_providers"):
        await _await_if_needed(app._persist_custom_providers())

    app.state.add_toast(f"✓ Provedor '{name}' removido.", persistent=False)
