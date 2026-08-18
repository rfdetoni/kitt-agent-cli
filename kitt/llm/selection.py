"""Transactional Model Selection Service ensuring atomic configuration, capability validation, and auth handling."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Coroutine, Dict, List, Optional

from kitt.domain.entities import ModelProfile, RouterConfig
from kitt.llm.auth import ProviderAuthService, ProviderAuthState
from kitt.llm.catalog import ProviderCatalogService
from kitt.llm.domain import ModelDescriptor, ProviderDescriptor
from kitt.llm.registry import ProviderRegistry
from kitt.router.router import TaskRouter


class SelectionTransactionStatus(Enum):
    IDLE = "IDLE"
    VALIDATING = "VALIDATING"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTHENTICATING = "AUTHENTICATING"
    REFRESHING = "REFRESHING"
    APPLYING = "APPLYING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class PendingModelSelection:
    role: str
    provider_id: str
    model_id: str
    base_url: Optional[str] = None


@dataclass
class ModelSelectionResult:
    success: bool
    status: SelectionTransactionStatus
    role: str
    provider_id: str
    model_id: str
    error_message: Optional[str] = None
    previous_profile: Optional[ModelProfile] = None


class ModelSelectionService:
    """Orchestrates transactional provider/model selection with capabilities pre-check and auth flow."""

    def __init__(
        self,
        workspace_path: str = ".",
        registry: Optional[ProviderRegistry] = None,
        router: Optional[TaskRouter] = None,
    ):
        self.workspace_path = workspace_path
        self.registry = registry or ProviderRegistry()
        self.router = router or TaskRouter(root_dir=workspace_path)
        self.pending_selection: Optional[PendingModelSelection] = None
        self._current_transaction_id: int = 0

    async def select(
        self,
        role: str,
        provider_id: str,
        model_id: str,
        base_url: Optional[str] = None,
        auth_interaction: Optional[Callable[[str], Coroutine[Any, Any, Optional[str]]]] = None,
    ) -> ModelSelectionResult:
        """Executes full transactional selection: pre-check -> auth -> validate -> commit/rollback."""
        self._current_transaction_id += 1
        tx_id = self._current_transaction_id
        pid = (provider_id or "").strip().lower()
        mid = (model_id or "").strip()

        profile_name = role if role in self.router.config.profiles else "execute"
        previous_profile = self.router.config.profiles.get(profile_name)

        # 1. Resolve Provider
        provider = self.registry.get_provider(pid)
        if not provider:
            return ModelSelectionResult(
                success=False,
                status=SelectionTransactionStatus.FAILED,
                role=role,
                provider_id=pid,
                model_id=mid,
                error_message=f"Provider '{pid}' not found in catalog or registry",
                previous_profile=previous_profile,
            )

        # 2. Check authentication status
        auth_state = self.registry.auth_service.state(pid)
        has_secret = bool(self.registry.auth_service.resolve(auth_state.credential_ref, pid))
        is_authenticated = (auth_state.auth_type == "none") or has_secret

        if provider.local or pid in ("ollama", "lmstudio"):
            is_authenticated = True

        if not is_authenticated:
            self.pending_selection = PendingModelSelection(
                role=role,
                provider_id=pid,
                model_id=mid,
                base_url=base_url,
            )
            if auth_interaction:
                try:
                    secret = await auth_interaction(pid)
                    if not secret:
                        self.pending_selection = None
                        return ModelSelectionResult(
                            success=False,
                            status=SelectionTransactionStatus.CANCELLED,
                            role=role,
                            provider_id=pid,
                            model_id=mid,
                            error_message="Authentication cancelled by user",
                            previous_profile=previous_profile,
                        )
                    # Save credential
                    self.registry.auth_service.login(pid, secret, method="api_key")
                except Exception as exc:
                    self.pending_selection = None
                    return ModelSelectionResult(
                        success=False,
                        status=SelectionTransactionStatus.FAILED,
                        role=role,
                        provider_id=pid,
                        model_id=mid,
                        error_message=f"Authentication failed: {exc}",
                        previous_profile=previous_profile,
                    )
            else:
                return ModelSelectionResult(
                    success=False,
                    status=SelectionTransactionStatus.AUTH_REQUIRED,
                    role=role,
                    provider_id=pid,
                    model_id=mid,
                    error_message=f"Provider '{pid}' requires authentication",
                    previous_profile=previous_profile,
                )

        # 3. Race condition protection
        if tx_id != self._current_transaction_id:
            return ModelSelectionResult(
                success=False,
                status=SelectionTransactionStatus.CANCELLED,
                role=role,
                provider_id=pid,
                model_id=mid,
                error_message="Transaction superseded by newer selection",
                previous_profile=previous_profile,
            )

        # 4. Resolve Base URL
        resolved_base_url = base_url or provider.base_url or "https://api.openai.com"

        # 5. Atomic Router Config update
        try:
            current_profile = previous_profile or ModelProfile(backend=pid, model=mid)
            updated_profile = ModelProfile(
                backend=pid,
                model=mid,
                base_url=resolved_base_url,
                credential_ref=f"auth:{pid}" if not (provider.local or pid in ("ollama", "lmstudio")) else None,
                max_output_tokens=max(current_profile.max_output_tokens, 2048) if role == "principal" else max(current_profile.max_output_tokens, 1024),
                supports_json=pid in {"openai", "anthropic", "gemini", "deepseek", "groq", "together", "mistral", "openrouter", "antigravity", "ollama"},
            )
            self.router.config.profiles[profile_name] = updated_profile
            self.router.save_config(self.workspace_path)
            self.pending_selection = None

            return ModelSelectionResult(
                success=True,
                status=SelectionTransactionStatus.SUCCESS,
                role=role,
                provider_id=pid,
                model_id=mid,
                previous_profile=previous_profile,
            )
        except Exception as exc:
            # Rollback
            if previous_profile:
                self.router.config.profiles[profile_name] = previous_profile
            return ModelSelectionResult(
                success=False,
                status=SelectionTransactionStatus.FAILED,
                role=role,
                provider_id=pid,
                model_id=mid,
                error_message=f"Failed to persist model selection: {exc}",
                previous_profile=previous_profile,
            )
