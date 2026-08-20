"""ProviderRegistry unifying catalog, authentication, custom providers, and protocol adapters."""
from __future__ import annotations

from typing import Dict, List, Optional, Set
from kitt.llm.auth import ProviderAuthService
from kitt.llm.catalog import ProviderCatalogService
from kitt.llm.endpoint_security import (
    ProviderEndpointTrustStore,
    resolve_endpoint_credential,
)
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


class ProviderRegistry:
    """Registry coordinating catalog descriptors, custom providers, auth, and runtime adapters."""

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

        # Protocol adapters
        self._adapters_by_protocol: Dict[str, ProviderAdapter] = {
            "ollama-chat": OllamaAdapter(),
            "openai-chat-completions": OpenAIChatAdapter(),
            "openai-responses": OpenAIResponsesAdapter(),
            "anthropic-messages": AnthropicAdapter(),
            "gemini-generate-content": GeminiAdapter(),
        }

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
        catalog_provider = self.catalog.provider(pid)
        if catalog_provider is not None and catalog_provider.source == "builtin":
            raise ValueError(
                f"Custom provider '{pid}' cannot shadow built-in provider identity"
            )
        desc = ProviderDescriptor(
            id=pid,
            name=name,
            protocol=protocol,
            base_url=base_url,
            source="custom",
        )
        self._custom_providers[pid] = desc
        if models:
            self._custom_models[pid] = models
        return desc

    def set_model_whitelist(self, provider_id: str, model_ids: List[str]) -> None:
        pid = provider_id.strip().lower()
        self._whitelist_models[pid] = set(model_ids)

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
        for p in self.catalog.providers():
            if p.id not in seen:
                all_p.append(p)
                seen.add(p.id)
        return all_p

    def get_adapter_for_protocol(self, protocol: str) -> ProviderAdapter:
        adapter = self._adapters_by_protocol.get(protocol)
        if adapter:
            return adapter
        return self._adapters_by_protocol["openai-chat-completions"]

    def get_adapter_for_provider(self, provider_id: str) -> ProviderAdapter:
        p = self.get_provider(provider_id)
        if p and p.protocol:
            return self.get_adapter_for_protocol(p.protocol)
        pid = (provider_id or "").strip().lower()
        if "ollama" in pid:
            return self._adapters_by_protocol["ollama-chat"]
        if "anthropic" in pid or "claude" in pid:
            return self._adapters_by_protocol["anthropic-messages"]
        if "gemini" in pid or "google" in pid:
            return self._adapters_by_protocol["gemini-generate-content"]
        return self._adapters_by_protocol["openai-chat-completions"]

    def discover_runtime_models(
        self, provider_id: str, base_url: Optional[str] = None, timeout: float = 5.0
    ) -> ModelDiscoveryResult:
        p = self.get_provider(provider_id)
        if not p:
            return ModelDiscoveryResult(
                status=ProviderDiscoveryStatus.UNSUPPORTED, message=f"Unknown provider '{provider_id}'"
            )

        target_base = base_url or p.base_url
        if not target_base:
            return ModelDiscoveryResult(
                status=ProviderDiscoveryStatus.UNSUPPORTED,
                message=f"Provider '{p.id}' has no endpoint",
            )
        try:
            api_key = resolve_endpoint_credential(
                self.auth_service,
                p.id,
                target_base,
                policy=self.endpoint_policy,
            )
        except Exception as exc:
            return ModelDiscoveryResult(
                status=ProviderDiscoveryStatus.AUTH_REQUIRED,
                message=str(exc),
            )

        adapter = self.get_adapter_for_protocol(p.protocol)
        result = adapter.list_models(
            base_url=target_base,
            api_key=api_key,
            timeout=timeout,
        )

        # Apply whitelist and blacklist
        if result.status == ProviderDiscoveryStatus.SUCCESS:
            filtered = []
            whitelist = self._whitelist_models.get(p.id)
            for m in result.models:
                if m.id in self._blacklist_models:
                    continue
                if whitelist is not None and m.id not in whitelist:
                    continue
                filtered.append(m)
            result.models = filtered
            if not filtered:
                result.status = ProviderDiscoveryStatus.NO_MODELS

        return result

    def effective_models(self, provider_id: str) -> List[ModelDescriptor]:
        """Combines runtime models (if accessible) and catalog/custom models."""
        p = self.get_provider(provider_id)
        if not p:
            return []

        # 1. Custom models
        if p.id in self._custom_models:
            return self._custom_models[p.id]

        # 2. Try runtime discovery
        res = self.discover_runtime_models(p.id, timeout=2.0)
        if res.status == ProviderDiscoveryStatus.SUCCESS and res.models:
            return res.models

        # 3. Fallback to catalog models
        return self.catalog.models(p.id)

    def health(self, provider_id: str, base_url: Optional[str] = None, timeout: float = 5.0) -> ProviderHealth:
        p = self.get_provider(provider_id)
        if not p:
            return ProviderHealth(status="unsupported", error_code="UNKNOWN_PROVIDER")

        target_base = base_url or p.base_url
        if not target_base:
            return ProviderHealth(
                status="unsupported",
                error_code="MISSING_ENDPOINT",
            )
        try:
            api_key = resolve_endpoint_credential(
                self.auth_service,
                p.id,
                target_base,
                policy=self.endpoint_policy,
            )
        except Exception:
            return ProviderHealth(
                status="unhealthy",
                authenticated=False,
                error_code="UNTRUSTED_ENDPOINT",
            )
        adapter = self.get_adapter_for_protocol(p.protocol)
        return adapter.health(
            base_url=target_base,
            api_key=api_key,
            timeout=timeout,
        )
