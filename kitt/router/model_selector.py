"""Model selector, provider discovery, and role assignment configuration."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from kitt.domain.entities import ModelProfile, RouterConfig
from kitt.llm.auth import ProviderAuthService
from kitt.llm.domain import (
    ModelDescriptor,
    ModelDiscoveryResult,
    ProviderDiscoveryStatus,
)
from kitt.llm.registry import ProviderRegistry
from kitt.router.router import TaskRouter

ROLES = ["main_chat", "context", "commit", "edit", "code_generation"]


def discover_models_typed(
    provider: str,
    base_url: str = "",
    api_key: str = "",
    timeout: float = 4.0,
    registry: Optional[ProviderRegistry] = None,
) -> ModelDiscoveryResult:
    """Performs typed model discovery from provider endpoint without inventing fake models."""
    reg = registry or ProviderRegistry()
    pid = (provider or "").strip().lower()

    if api_key:
        reg.auth_service.login(pid, api_key, method="session")

    return reg.discover_runtime_models(pid, base_url=base_url or None, timeout=timeout)


def fetch_provider_models(
    provider: str,
    base_url: str = "",
    api_key: str = "",
    timeout: float = 4.0,
    registry: Optional[ProviderRegistry] = None,
) -> List[str]:
    """Issues an HTTP GET request to discover model names dynamically from the provider API."""
    result = discover_models_typed(provider, base_url, api_key, timeout, registry)
    if result.status == ProviderDiscoveryStatus.SUCCESS:
        return [m.id for m in result.models]

    # Fallback to catalog known models for this provider
    reg = registry or ProviderRegistry()
    cat_models = reg.catalog.models(provider)
    return [m.id for m in cat_models]


class ModelConfigurator:
    """Discovers provider models and manages role assignment including Main Chat Model."""

    def __init__(self, root_dir: str = ".", registry: Optional[ProviderRegistry] = None):
        self.root_dir = root_dir
        self.router = TaskRouter(root_dir=root_dir)
        self.registry = registry or ProviderRegistry()

    def fetch_ollama_models(self, base_url: str = "http://localhost:11434") -> List[str]:
        return fetch_provider_models("ollama", base_url=base_url, registry=self.registry)

    def assign_roles(
        self,
        model_a: str,
        model_b: str,
        model_a_roles: List[str],
        backend_a: str = "ollama",
        base_url_a: str = "http://localhost:11434",
        api_key_a: str = "",
        backend_b: str = "ollama",
        base_url_b: str = "http://localhost:11434",
        api_key_b: str = "",
    ) -> RouterConfig:
        model_b_roles = [r for r in ROLES if r not in model_a_roles]

        # Login credentials securely to CredentialStore
        auth_a = self.registry.auth_service.login(backend_a, api_key_a) if api_key_a else None
        auth_b = self.registry.auth_service.login(backend_b, api_key_b) if api_key_b else None

        profiles = {
            "model_a": ModelProfile(
                backend=backend_a,
                model=model_a,
                base_url=base_url_a,
                credential_ref=auth_a.credential_ref if auth_a else None,
            ),
            "model_b": ModelProfile(
                backend=backend_b,
                model=model_b,
                base_url=base_url_b,
                credential_ref=auth_b.credential_ref if auth_b else None,
            ),
        }

        routing: Dict[str, str] = {}
        for role in model_a_roles:
            if role == "main_chat":
                routing["chat"] = "model_a"
            elif role == "context":
                routing["context-gather"] = "model_a"
                routing["summarize"] = "model_a"
            elif role == "commit":
                routing["validate-diff"] = "model_a"
            elif role == "edit":
                routing["code-edit"] = "model_a"
            elif role == "code_generation":
                routing["code-generation"] = "model_a"

        for role in model_b_roles:
            if role == "main_chat":
                routing["chat"] = "model_b"
            elif role == "context":
                routing["context-gather"] = "model_b"
                routing["summarize"] = "model_b"
            elif role == "commit":
                routing["validate-diff"] = "model_b"
            elif role == "edit":
                routing["code-edit"] = "model_b"
            elif role == "code_generation":
                routing["code-generation"] = "model_b"

        config = RouterConfig(profiles=profiles, routing=routing)
        self.router.config = config
        self.save_config(config)
        return config

    def save_config(self, config: RouterConfig) -> None:
        self.router.config = config
        self.router.save_config(self.root_dir)
