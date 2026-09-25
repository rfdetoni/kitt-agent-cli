from __future__ import annotations

import asyncio
import os
from dataclasses import replace

def _parse_model_command(ui, argument: str) -> tuple[str, str, str | None, str | None]:
    role, separator, remainder = argument.partition(" ")
    aliases = {"main": "principal", "primary": "principal", "execution": "principal", "execute": "principal", "ctx": "context"}
    role = aliases.get(role.lower(), role.lower())
    provider, separator2, model = remainder.strip().partition(" ")
    valid_roles = (*ui.model_setup_model.roles, "all")
    if role in valid_roles and separator and provider in ui.model_setup_model.providers and separator2 and model.strip():
        model_name, _, base_url = model.strip().partition(" ")
        return role, model_name, provider, base_url.strip() or None
    if role in valid_roles and separator and remainder.strip():
        return role, remainder.strip(), None, None
    return "principal", argument.strip(), None, None


def _role_tasks(ui, role: str) -> tuple[str, tuple[str, ...]]:
    if role == "context":
        return "context", ("context-gather", "summarize")
    if role == "validation":
        return "validation", ("validate-diff",)
    return "execute", ("chat", "code-generation", "code-edit")


def _model_for_role(ui, role: str) -> str:
    router = ui.runtime.processor.router
    profile_name, tasks = ui._role_tasks(role)
    selected = router.config.routing.get(tasks[0], profile_name)
    profile = router.config.profiles.get(selected) or router.config.profiles.get(profile_name)
    return profile.model if profile else "unconfigured"


def _profile_for_role(ui, role: str):
    router = ui.runtime.processor.router
    profile_name, tasks = ui._role_tasks(role)
    selected = router.config.routing.get(tasks[0], profile_name)
    return router.config.profiles.get(selected) or router.config.profiles.get(profile_name)


async def _set_model_role(ui, role: str, model: str, provider: str | None = None, base_url: str | None = None) -> None:
    router = ui.runtime.processor.router
    profile_name, tasks = ui._role_tasks(role)
    fallback = router.config.profiles.get(profile_name)
    if fallback is None:
        fallback = router.config.profiles.get("execute") or router.config.profiles.get("context")
    if fallback is None:
        raise RuntimeError("No provider profile available")
    provider = provider or fallback.backend
    same_provider = fallback.backend == provider
    default_url, _ = ui._provider_defaults(provider)
    custom_entry = None
    if hasattr(ui, "model_setup_model"):
        custom_entry = next((cp for cp in ui.model_setup_model.custom_providers if cp["name"] == provider), None)
    if not custom_entry:
        custom_entry = next((cp for cp in getattr(router.config, "custom_providers", []) if cp.get("name") == provider), None)
    if custom_entry and custom_entry.get("base_url"):
        default_url = custom_entry["base_url"]
    if base_url:
        target_url = base_url
    elif custom_entry and custom_entry.get("base_url"):
        target_url = custom_entry["base_url"]
    elif fallback.backend == provider:
        target_url = fallback.base_url
    else:
        target_url = default_url
    if target_url and not target_url.startswith(("http://", "https://")):
        target_url = f"http://{target_url}"
    is_kitt_proxy = "kitt-reverse-proxy" in (provider or "").lower() or "kitt-proxy" in (provider or "").lower() or ":3000" in (target_url or "")
    protocol = "ollama-chat" if (":11434" in (target_url or "") or "ollama" in (provider or "").lower()) else ("kitt-reverse-proxy" if is_kitt_proxy else fallback.protocol)
    router.config.profiles[profile_name] = replace(
        fallback, model=model, backend=provider,
        base_url=target_url,
        protocol=protocol,
        api_key=fallback.api_key if same_provider else "",
        credential_ref=(
            fallback.credential_ref if same_provider else None
        ),
        max_output_tokens=max(fallback.max_output_tokens, 2048) if role == "principal" else max(fallback.max_output_tokens, 1024),
        supports_json=provider in {"openai", "anthropic", "gemini", "deepseek", "groq", "together", "mistral", "openrouter", "antigravity", "ollama", "kitt-reverse-proxy", "kitt-proxy"} or "ollama" in (provider or "").lower() or is_kitt_proxy,
        enforce_local_limits=(
            getattr(fallback, "enforce_local_limits", True)
            if same_provider
            else (False if is_kitt_proxy else True)
        ),
    )
    for task in tasks:
        router.config.routing[task] = profile_name
    await ui._run_blocking(router.save_config, ui.state.workspace_path)
    if await ui._ensure_daemon_management():
        await ui.bridge.reload_router()
    ui._init_models_from_runtime()
    ui.state.add_toast(f"{role.title()} model: {provider}/{model}")


async def _toggle_role_local_limits(ui, role: str) -> None:
    router = ui.runtime.processor.router
    profile_name, _ = ui._role_tasks(role)
    prof = router.config.profiles.get(profile_name)
    if not prof:
        return
    curr = getattr(prof, "enforce_local_limits", True)
    router.config.profiles[profile_name] = replace(prof, enforce_local_limits=not curr)
    await ui._run_blocking(router.save_config, ui.state.workspace_path)
    if await ui._ensure_daemon_management():
        await ui.bridge.reload_router()
    status_str = "ATIVADOS" if not curr else "DESATIVADOS"
    ui.state.add_toast(f"Limites locais {status_str} para o cargo {role.title()}.", persistent=False)
    if ui.application:
        ui.application.invalidate()



async def _models_for_provider(ui, provider: str, base_url: str) -> list[str]:
    from kitt.llm.auth import ProviderAuthService
    from kitt.llm.catalog import ProviderCatalogService
    from kitt.llm.endpoint_security import (
        ProviderEndpointTrustStore,
        resolve_endpoint_credential,
    )
    from kitt.router.model_selector import ModelConfigurator, fetch_provider_models

    norm_url = (base_url or "").strip().rstrip("/")
    if norm_url and not norm_url.startswith(("http://", "https://")):
        norm_url = f"http://{norm_url}"

    endpoint_policy = ProviderEndpointTrustStore()
    endpoint_allowed = bool(
        norm_url
        and (
            endpoint_policy.is_trusted(provider, norm_url)
            or ui._is_local_or_no_auth_provider(provider, norm_url)
        )
    )
    api_key = None
    if norm_url and endpoint_policy.is_trusted(provider, norm_url):
        try:
            api_key = resolve_endpoint_credential(
                ProviderAuthService(),
                provider,
                norm_url,
                policy=endpoint_policy,
            )
        except Exception:
            api_key = None

    # 1. Live discovery for Ollama (local, remote LAN/WAN IP, or :11434 port)
    is_ollama = (
        provider == "ollama"
        or "11434" in norm_url
        or "ollama" in provider.lower()
        or "ollama" in norm_url.lower()
    )
    if is_ollama and norm_url and endpoint_allowed:
        try:
            models = await ui._run_blocking(ModelConfigurator(ui.state.workspace_path).fetch_ollama_models, norm_url)
            if models:
                return list(dict.fromkeys(models))
        except Exception:
            pass

    # 2. Live discovery for OpenAI-compatible endpoints (LM Studio, vLLM, LocalAI, custom servers, etc.)
    if norm_url and endpoint_allowed:
        try:
            models = await ui._run_blocking(fetch_provider_models, provider, norm_url, api_key, 2.5)
            if models and models != [f"{provider}-default"]:
                return list(dict.fromkeys(models))
        except Exception:
            pass

        # Also try querying OpenAI /v1/models endpoint adapter as fallback for custom servers
        try:
            models = await ui._run_blocking(fetch_provider_models, "openai", norm_url, api_key, 2.5)
            if models and models != ["openai-default"]:
                return list(dict.fromkeys(models))
        except Exception:
            pass

    # 3. Dynamic Models.dev catalog lookup
    try:
        cat = ProviderCatalogService()
        cat_models = [m.id for m in cat.models(provider)]
        if cat_models:
            return cat_models
    except Exception:
        pass

    builtin_fallbacks = {
        "openai": [
            "gpt-4o",
            "gpt-4o-mini",
            "gpt-4.1",
            "gpt-4.1-mini",
            "gpt-4.1-nano",
            "o4-mini",
        ],
        "anthropic": ["claude-3-7-sonnet", "claude-3-5-sonnet"],
        "gemini": ["gemini-1.5-pro", "gemini-1.5-flash"],
        "deepseek": ["deepseek-v3", "deepseek-r1"],
        "groq": ["llama-3.3-70b-versatile"],
        "mistral": ["mistral-large-latest"],
        "openrouter": ["openrouter/auto"],
        "antigravity": ["antigravity-chat-latest"],
        "kitt-reverse-proxy": ["chatgpt-web"],
        "kitt-proxy": ["chatgpt-web"],
    }
    if provider in builtin_fallbacks:
        return builtin_fallbacks[provider]

    return [f"{provider}-default"]


async def _prepare_model_setup(ui, base_url: str | None = None, provider: str | None = None) -> None:
    ui.state.status_text = "DISCOVERING MODELS"
    profile = ui._profile_for_role(ui.model_setup_model.selected_role)
    norm_override = base_url.strip().rstrip("/") if base_url else None
    if norm_override and not norm_override.startswith(("http://", "https://")):
        norm_override = f"http://{norm_override}"
    ui.model_setup_model.base_url_override = norm_override
    
    if provider:
        if provider in ui.model_setup_model.providers:
            ui.model_setup_model.provider_index = ui.model_setup_model.providers.index(provider)
    elif norm_override:
        if "ollama" in ui.model_setup_model.providers and ":11434" in norm_override:
            ui.model_setup_model.provider_index = ui.model_setup_model.providers.index("ollama")
    else:
        # First time load: sync with current role's profile backend
        if profile and profile.backend in ui.model_setup_model.providers and not ui.model_setup_model.models:
            ui.model_setup_model.provider_index = ui.model_setup_model.providers.index(profile.backend)
    
    active_provider = ui.model_setup_model.selected_provider
    default_url, _ = ui._provider_defaults(active_provider)

    # Check custom providers for registered base_url
    custom_entry = next((cp for cp in ui.model_setup_model.custom_providers if cp["name"] == active_provider), None)
    if custom_entry and custom_entry.get("base_url"):
        default_url = custom_entry["base_url"]

    endpoint = ui.model_setup_model.base_url_override or (profile.base_url if profile and profile.backend == active_provider else default_url)
    if endpoint and not endpoint.startswith(("http://", "https://")):
        endpoint = f"http://{endpoint}"
    
    models = await ui._models_for_provider(active_provider, endpoint)
    ui.model_setup_model.models = list(dict.fromkeys(models)) if models else ["default-model"]
    
    selected = ui._model_for_role(ui.model_setup_model.selected_role)
    if selected in ui.model_setup_model.models:
        ui.model_setup_model.model_index = ui.model_setup_model.models.index(selected)
    else:
        ui.model_setup_model.model_index = 0
    ui.state.status_text = "SYSTEM ONLINE"
    if ui.application:
        ui.application.invalidate()


def _model_setup_search_changed(ui) -> None:
    ui.model_setup_model.search_query = ui.model_setup_search_buffer.text
    ui.model_setup_model.model_index = 0
    if ui.application:
        ui.application.invalidate()


async def _open_model_setup_overlay(ui, base_url: str | None = None, provider: str | None = None) -> None:
    await ui._prepare_model_setup(base_url, provider=provider)
    ui.model_setup_search_buffer.text = ""
    ui.model_setup_model.search_query = ""
    ui.open_overlay("model_setup", ui.model_setup_search_control)



def _provider_defaults(provider: str) -> tuple[str, str]:
    defaults = {
        "ollama": (os.environ.get("OLLAMA_HOST", "http://localhost:11434"), ""),
        "lmstudio": (os.environ.get("LMSTUDIO_HOST", "http://localhost:1234"), ""),
        "openai": ("https://api.openai.com", os.environ.get("OPENAI_API_KEY", "")),
        "anthropic": ("https://api.anthropic.com", os.environ.get("ANTHROPIC_API_KEY", "")),
        "gemini": ("https://generativelanguage.googleapis.com", os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")),
        "deepseek": ("https://api.deepseek.com", os.environ.get("DEEPSEEK_API_KEY", "")),
        "groq": ("https://api.groq.com/openai", os.environ.get("GROQ_API_KEY", "")),
        "together": ("https://api.together.xyz", os.environ.get("TOGETHER_API_KEY", "")),
        "mistral": ("https://api.mistral.ai", os.environ.get("MISTRAL_API_KEY", "")),
        "openrouter": ("https://openrouter.ai/api", os.environ.get("OPENROUTER_API_KEY", "")),
        "xai": ("https://api.xai.com", os.environ.get("XAI_API_KEY", "")),
        "fireworks": ("https://api.fireworks.ai/inference", os.environ.get("FIREWORKS_API_KEY", "")),
        "cohere": ("https://api.cohere.com", os.environ.get("COHERE_API_KEY", "")),
        "azure": (os.environ.get("AZURE_OPENAI_ENDPOINT", "https://your-resource.openai.azure.com"), os.environ.get("AZURE_OPENAI_API_KEY", "")),
        "antigravity": ("https://api.antigravity.dev", os.environ.get("ANTIGRAVITY_API_KEY", "")),
        "kitt-reverse-proxy": (os.environ.get("KITT_REVERSE_PROXY_URL", "http://127.0.0.1:3000"), ""),
        "kitt-proxy": (os.environ.get("KITT_REVERSE_PROXY_URL", "http://127.0.0.1:3000"), ""),
    }
    if provider in defaults:
        return defaults[provider]
    p_lower = (provider or "").strip().lower()
    if "ollama" in p_lower:
        return (os.environ.get("OLLAMA_HOST", "http://localhost:11434"), "")
    if "lmstudio" in p_lower:
        return (os.environ.get("LMSTUDIO_HOST", "http://localhost:1234"), "")
    try:
        from kitt.llm.catalog import ProviderCatalogService
        cat = ProviderCatalogService()
        cat_p = cat.provider(provider)
        if cat_p and cat_p.base_url:
            env_val = os.environ.get(cat_p.env_vars[0], "") if cat_p.env_vars else ""
            return (cat_p.base_url, env_val)
    except Exception:
        pass
    env_key = os.environ.get(f"{provider.upper().replace('-', '_').replace(' ', '_')}_API_KEY", "")
    env_host = os.environ.get(f"{provider.upper().replace('-', '_').replace(' ', '_')}_HOST", "http://localhost:11434" if "ollama" in p_lower else "http://localhost:8000/v1")
    return (env_host, env_key)

_set_model_role = _model_service._set_model_role
_toggle_role_local_limits = _model_service._toggle_role_local_limits
_models_for_provider = _model_service._models_for_provider
_prepare_model_setup = _model_service._prepare_model_setup
_model_setup_search_changed = _model_service._model_setup_search_changed
_open_model_setup_overlay = _model_service._open_model_setup_overlay
