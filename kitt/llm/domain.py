"""Domain entities, enums, descriptors and exception hierarchy for LLM providers."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Tuple


class ProviderDiscoveryStatus(Enum):
    SUCCESS = "SUCCESS"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTH_INVALID = "AUTH_INVALID"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    UNREACHABLE = "UNREACHABLE"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    UNSUPPORTED = "UNSUPPORTED"
    NO_MODELS = "NO_MODELS"


# --- Provider & Model Descriptors ---

@dataclass(frozen=True)
class ProviderDescriptor:
    id: str
    name: str
    protocol: Optional[str] = None  # e.g. "ollama-chat", "openai-chat-completions", "openai-responses", "anthropic-messages", "gemini-generate-content"
    base_url: Optional[str] = None
    default_base_url: Optional[str] = None
    env_vars: Tuple[str, ...] = ()
    auth_methods: Tuple[str, ...] = ("api_key",)
    local: bool = False
    custom: bool = False
    supports_model_discovery: bool = True
    supports_custom_base_url: bool = True
    source: str = "models.dev"
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelDescriptor:
    provider_id: str
    id: str
    name: str
    context_window: Optional[int] = 8192
    max_output_tokens: Optional[int] = 4096
    supports_tools: bool = False
    supports_reasoning: bool = False
    supports_temperature: bool = True
    supports_attachments: bool = False
    input_modalities: FrozenSet[str] = frozenset({"text"})
    output_modalities: FrozenSet[str] = frozenset({"text"})
    cost_input: Optional[Decimal] = None
    cost_output: Optional[Decimal] = None
    raw_metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class ModelDiscoveryResult:
    status: ProviderDiscoveryStatus
    models: List[ModelDescriptor] = field(default_factory=list)
    message: Optional[str] = None


@dataclass
class ProviderHealth:
    status: str  # "healthy", "degraded", "unhealthy", "unreachable"
    latency_ms: Optional[float] = None
    authenticated: Optional[bool] = None
    models_available: Optional[int] = None
    error_code: Optional[str] = None


# --- Exception Hierarchy ---

class ProviderError(Exception):
    """Base exception for all provider operations."""


class ProviderAuthError(ProviderError):
    """Raised when authentication fails (401, 403, missing credentials)."""


class ProviderRateLimitError(ProviderError):
    """Raised when rate limits are exceeded (429)."""
    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


class ProviderTimeoutError(ProviderError):
    """Raised when network or API request times out."""


class ProviderConnectionError(ProviderError):
    """Raised when endpoint is unreachable, DNS fails, or connection is refused."""


class ProviderProtocolError(ProviderError):
    """Raised when API returns malformed JSON or unexpected schema."""


class ProviderModelNotFoundError(ProviderError):
    """Raised when requested model is not found in provider (404)."""


class ProviderCapabilityError(ProviderError):
    """Raised when model does not support a required capability (e.g. tools)."""
