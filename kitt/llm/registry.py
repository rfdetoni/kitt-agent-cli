"""Provider registry unifying catalog, authentication and protocol adapters."""
from __future__ import annotations

import ipaddress
import urllib.parse
from typing import Dict, List, Optional, Set

from kitt.llm.auth import ProviderAuthService
from kitt.llm.catalog import ProviderCatalogService
from kitt.llm.endpoint_security import ProviderEndpointTrustStore, resolve_endpoint_credential
from kitt.llm.domain import (
    ModelDescriptor,
    ModelDiscoveryResult,
    ProviderDescriptor,
    ProviderDiscoveryStatus,
    ProviderHealth,
)
from kitt.llm.providers import (
    AnthropicAdapter,
    GeminiAdapter,
    OllamaAdapter,
    OpenAIChatAdapter,
    OpenAICompatibleAdapter,
    OpenAIResponsesAdapter,
    ProviderAdapter,
)


class UnsupportedProviderProtocol(ValueError):
    """Raised when a provider declares a protocol KITT cannot execute."""


class ProviderRegistry:
    """Registry coordinating descriptors, auth, discovery and runtime adapters."""

    def __init__(
        self,
        catalog: Optional[ProviderCatalogService] = None,
        auth_service: Optional[ProviderAuthService] = None,
        endpoint_policy: Optional[ProviderEndpointTrustStore] = None,
    ):
        self.catalog = catalog or ProviderCatalogService()
        self.auth_service = auth_service or ProviderAuthService()
        self.endpoint_policy = endpoint_policy or ProviderEndpointTrustStore()
        self._custom_providers: Dict[str, ProviderDescriptor] = {}
        self._custom_models: Dict[str, List[ModelDescriptor]] = {}
        self._whitelist_models: Dict[str, Set[str]] = {}
        self._blacklist_models: Set[str] = set()

        self._adapters_by_protocol: Dict[str, ProviderAdapter] = {
            "ollama-chat": OllamaAdapter(),
            "openai-chat-completions": OpenAIChatAdapter(),
            "openai-compatible": OpenAICompatibleAdapter(),
            "openai-responses": OpenAIResponsesAdapter(),
            "anthropic-messages": AnthropicAdapter(),
            "gemini-generate-content": GeminiAdapter(),
        }

    @property
    def supported_protocols(self) -> tuple[str, ...]:
        return tuple(sorted(self._adapters_by_protocol))

    def _normalize_protocol(self, protocol: str) -> str:
        value = (protocol or "").strip().lower()
        if value not in self._adapters_by_protocol:
            supported = ", ".join(self.supported_protocols)
            raise UnsupportedProviderProtocol(
                f"Unsupported provider protocol '{protocol}'. Supported protocols: {supported}"
            )
        return value

    def register_custom_provider(
        self,
        provider_id: str,
        name: str,
        protocol: str = "openai-chat-completions",
        base_url: Optional[str] = None,
        models: Optional[List[ModelDescriptor]] = None,
    ) -> ProviderDescriptor:
        pid = provider_id.strip().lower()
        if not pid:
            raise ValueError("Custom provider id cannot be empty")
        normalized_protocol = self._normalize_protocol(protocol)
        catalog_provider = self.catalog.provider(pid)
        if catalog_provider is not None and catalog_provider.source == "builtin":
            raise ValueError(
                f"Custom provider '{pid}' cannot shadow built-in provider identity"
            )
        desc = ProviderDescriptor(
            id=pid,
            name=name,
            protocol=normalized_protocol,
            base_url=base_url,
            source="custom",
        )
        self._custom_providers[pid] = desc
        if models:
            self._custom_models[pid] = models
        return desc

    def set_model_whitelist(self, provider_id: str, model_ids: List[str]) -> None:
        self._whitelist_models[provider_id.strip().lower()] = set(model_ids)

    def set_model_blacklist(self, model_ids: List[str]) -> None:
        self._blacklist_models = set(model_ids)

    def get_provider(self, provider_id: str) -> Optional[ProviderDescriptor]:
        pid = (provider_id or "").strip().lower()
        if pid in self._custom_providers:
            return self._custom_providers[pid]
        return self.catalog.provider(pid)

    def list_providers(self) -> List[ProviderDescriptor]:
        all_p = list(self._custom_providers.values())
        seen = {p.id for p in all_p}
        for provider in self.catalog.providers():
            if provider.id not in seen:
                all_p.append(provider)
                seen.add(provider.id)
        return all_p

    def get_adapter_for_protocol(self, protocol: str) -> ProviderAdapter:
        normalized = self._normalize_protocol(protocol)
        return self._adapters_by_protocol[normalized]

    def get_adapter_for_provider(self, provider_id: str) -> ProviderAdapter:
        provider = self.get_provider(provider_id)
        if provider and provider.protocol:
            return self.get_adapter_for_protocol(provider.protocol)

        # Legacy descriptors without protocol remain supported by deterministic
        # identity inference. Unknown declared protocols never reach this path.
        pid = (provider_id or "").strip().lower()
        if "ollama" in pid:
            return self._adapters_by_protocol["ollama-chat"]
        if "anthropic" in pid or "claude" in pid:
            return self._adapters_by_protocol["anthropic-messages"]
        if "gemini" in pid or "google" in pid:
            return self._adapters_by_protocol["gemini-generate-content"]
        return self._adapters_by_protocol["openai-chat-completions"]

    def discover_runtime_models(
        self,
        provider_id: str,
        base_url: Optional[str] = None,
        timeout: float = 5.0,
    ) -> ModelDiscoveryResult:
        provider = self.get_provider(provider_id)
        if not provider:
            return ModelDiscoveryResult(
                status=ProviderDiscoveryStatus.UNSUPPORTED,
                message=f"Unknown provider '{provider_id}'",
            )

        target_base = base_url or provider.base_url
        if not target_base:
            return ModelDiscoveryResult(
                status=ProviderDiscoveryStatus.UNSUPPORTED,
                message=f"Provider '{provider.id}' has no endpoint",
            )
        try:
            adapter = self.get_adapter_for_protocol(provider.protocol)
        except UnsupportedProviderProtocol as exc:
            return ModelDiscoveryResult(
                status=ProviderDiscoveryStatus.UNSUPPORTED,
                message=str(exc),
            )

        api_key = None
        if not self._is_local_discovery_endpoint(
            provider.id,
            target_base,
            local=bool(getattr(provider, "local", False)),
        ):
            try:
                api_key = resolve_endpoint_credential(
                    self.auth_service,
                    provider.id,
                    target_base,
                    policy=self.endpoint_policy,
                )
            except Exception as exc:
                return ModelDiscoveryResult(
                    status=ProviderDiscoveryStatus.AUTH_REQUIRED,
                    message=str(exc),
                )

        result = adapter.list_models(
            base_url=target_base,
            api_key=api_key,
            timeout=timeout,
        )
        if result.status == ProviderDiscoveryStatus.SUCCESS:
            filtered = []
            whitelist = self._whitelist_models.get(provider.id)
            for model in result.models:
                if model.id in self._blacklist_models:
                    continue
                if whitelist is not None and model.id not in whitelist:
                    continue
                filtered.append(model)
            result.models = filtered
            if not filtered:
                result.status = ProviderDiscoveryStatus.NO_MODELS
        return result

    @staticmethod
    def _is_local_discovery_endpoint(
        provider_id: str,
        base_url: str,
        *,
        local: bool = False,
    ) -> bool:
        if local:
            return True
        pid = (provider_id or "").strip().lower()
        if pid in {"ollama", "lmstudio"}:
            return True
        try:
            host = (urllib.parse.urlsplit(base_url).hostname or "").strip().lower()
        except Exception:
            return False
        if host in {"localhost", "127.0.0.1", "::1"}:
            return True
        if host.endswith((".local", ".lan", ".internal")):
            return True
        try:
            ip = ipaddress.ip_address(host.split("%", 1)[0])
        except ValueError:
            return False
        return bool(ip.is_loopback or ip.is_private)

    def effective_models(self, provider_id: str) -> List[ModelDescriptor]:
        provider = self.get_provider(provider_id)
        if not provider:
            return []
        if provider.id in self._custom_models:
            return self._custom_models[provider.id]
        result = self.discover_runtime_models(provider.id, timeout=2.0)
        if result.status == ProviderDiscoveryStatus.SUCCESS and result.models:
            return result.models
        return self.catalog.models(provider.id)

    def health(
        self,
        provider_id: str,
        base_url: Optional[str] = None,
        timeout: float = 5.0,
    ) -> ProviderHealth:
        provider = self.get_provider(provider_id)
        if not provider:
            return ProviderHealth(status="unsupported", error_code="UNKNOWN_PROVIDER")
        target_base = base_url or provider.base_url
        if not target_base:
            return ProviderHealth(status="unsupported", error_code="MISSING_ENDPOINT")
        try:
            adapter = self.get_adapter_for_protocol(provider.protocol)
        except UnsupportedProviderProtocol:
            return ProviderHealth(status="unsupported", error_code="UNSUPPORTED_PROTOCOL")
        try:
            api_key = resolve_endpoint_credential(
                self.auth_service,
                provider.id,
                target_base,
                policy=self.endpoint_policy,
            )
        except Exception:
            return ProviderHealth(
                status="unhealthy",
                authenticated=False,
                error_code="UNTRUSTED_ENDPOINT",
            )
        return adapter.health(base_url=target_base, api_key=api_key, timeout=timeout)
