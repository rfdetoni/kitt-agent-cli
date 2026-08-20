"""Native Python HTTP client for supported LLM providers."""
from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import urllib.request
from typing import Dict, Generator, List, Optional

from kitt.domain.entities import ModelProfile
from kitt.llm.auth import ProviderAuthService
from kitt.llm.endpoint_security import (
    ProviderEndpointTrustStore,
    resolve_endpoint_credential,
)
from kitt.llm.domain import (
    ProviderAuthError,
    ProviderConnectionError,
    ProviderError,
    ProviderProtocolError,
    ProviderTimeoutError,
)
from kitt.llm.providers.base import LLMRequest
from kitt.llm.registry import ProviderRegistry
from kitt.llm.retry import RetryConfig, RetryPolicy
from kitt.router.models import ModelCapabilities


LLMError = ProviderError
LLMConnectionError = ProviderConnectionError
LLMTimeoutError = ProviderTimeoutError
LLMProtocolError = ProviderProtocolError


class LLMEmptyResponseError(LLMError):
    """Raised when a provider returns no visible response."""


class UnsupportedProviderError(LLMError):
    """Raised when a backend provider is unknown or unsupported."""


def _with_retry(fn, max_retries: int = 3, base_delay: float = 0.5):
    """Backward-compatible helper delegating retry behavior to RetryPolicy."""
    policy = RetryPolicy(
        RetryConfig(
            max_retries=max_retries,
            base_delay_ms=int(base_delay * 1000),
        )
    )
    yield from policy.execute_with_retry(fn)


class LLMClient:
    """Unified client delegating requests to protocol adapters."""

    LOCAL_BACKENDS = frozenset({"ollama", "lmstudio", "antigravity", "local"})

    def __init__(
        self,
        profile: ModelProfile,
        executor=None,
        retry_policy: Optional[RetryPolicy] = None,
        registry: Optional[ProviderRegistry] = None,
        auth_service: Optional[ProviderAuthService] = None,
        endpoint_policy: Optional[ProviderEndpointTrustStore] = None,
    ):
        self.profile = profile
        self._external_executor = executor is not None
        self._executor = executor or concurrent.futures.ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="llm_client_worker",
        )
        self.retry_policy = retry_policy or RetryPolicy()
        self.registry = registry or ProviderRegistry()
        self.auth_service = auth_service or self.registry.auth_service
        self.endpoint_policy = (
            endpoint_policy
            or getattr(self.registry, "endpoint_policy", None)
            or ProviderEndpointTrustStore()
        )

    @property
    def capabilities(self) -> ModelCapabilities:
        """Expose the existing routing capability contract to runtime selectors.

        KITT's host-tool protocol is textual and does not require a provider's
        native function-calling API. ``supports_native_tools`` therefore means
        that the KITT tool surface can be used by this client, while
        ``tool_call_reliability`` still reflects the profile's explicit tool
        support hint.
        """
        profile = self.profile
        backend = (profile.backend or "").lower()
        is_local = backend in self.LOCAL_BACKENDS
        tier = "small" if profile.context_window <= 8192 else "large"
        return ModelCapabilities(
            profile_name=profile.model or backend or "model",
            tier=tier,
            input_context_limit=max(1, int(profile.context_window)),
            max_output_tokens=max(1, int(profile.max_output_tokens)),
            supports_json=bool(profile.supports_json),
            supports_native_tools=True,
            tool_call_reliability=0.8 if profile.supports_tools else 0.6,
            code_edit_score=0.75 if tier == "small" else 0.9,
            reasoning_score=0.75 if tier == "small" else 0.9,
            languages=(),
            is_local=is_local,
            privacy_class="local" if is_local else "cloud",
        )

    def close(self):
        """Shut down the owned thread pool executor."""
        if self._executor and not self._external_executor:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def chat(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        response_format: Optional[str] = None,
    ) -> str:
        full_text = "".join(
            self.chat_stream(
                messages,
                system_prompt=system_prompt,
                response_format=response_format,
            )
        )
        if not full_text.strip():
            raise LLMEmptyResponseError("LLM returned empty response.")
        return full_text

    async def achat_stream(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        response_format: Optional[str] = None,
    ):
        queue: asyncio.Queue = asyncio.Queue(maxsize=128)
        loop = asyncio.get_running_loop()
        sentinel = object()
        stop = threading.Event()

        def worker():
            try:
                for chunk in self.chat_stream(
                    messages,
                    system_prompt,
                    response_format,
                ):
                    if stop.is_set():
                        break
                    asyncio.run_coroutine_threadsafe(
                        queue.put(("data", chunk)), loop
                    ).result(timeout=10.0)
                asyncio.run_coroutine_threadsafe(
                    queue.put(("done", sentinel)), loop
                )
            except Exception as exc:
                try:
                    asyncio.run_coroutine_threadsafe(
                        queue.put(("error", exc)), loop
                    )
                except RuntimeError:
                    pass

        future = self._executor.submit(worker)
        try:
            while True:
                message_type, payload = await queue.get()
                if message_type == "done":
                    break
                if message_type == "error":
                    raise payload
                if message_type == "data":
                    yield payload
        finally:
            stop.set()
            if not future.done():
                future.cancel()

    def chat_stream(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        response_format: Optional[str] = None,
    ) -> Generator[str, None, None]:
        backend = (self.profile.backend or "").strip().lower()
        base_url = (self.profile.base_url or "").strip()

        api_key = resolve_endpoint_credential(
            self.auth_service,
            backend,
            base_url,
            credential_ref=self.profile.credential_ref,
            raw_secret=self.profile.api_key,
            policy=self.endpoint_policy,
        )
        base_url_lower = base_url.lower()
        if self.profile.protocol:
            adapter = self.registry.get_adapter_for_protocol(self.profile.protocol)
        elif (
            ":11434" in base_url_lower
            or "ollama" in backend
            or "ollama" in base_url_lower
        ):
            adapter = self.registry.get_adapter_for_protocol("ollama-chat")
        elif backend:
            adapter = self.registry.get_adapter_for_provider(backend)
        else:
            adapter = self.registry.get_adapter_for_protocol(
                "openai-chat-completions"
            )

        request = LLMRequest(
            model=self.profile.model,
            messages=messages,
            system_prompt=system_prompt,
            response_format=response_format,
            temperature=self.profile.temperature,
            context_window=self.profile.context_window,
            max_output_tokens=self.profile.max_output_tokens,
            keep_alive=self.profile.keep_alive,
            api_key=api_key,
            base_url=self.profile.base_url,
            timeout_seconds=self.profile.request_timeout_seconds,
        )
        yield from self.retry_policy.execute_with_retry(
            lambda: adapter.stream(request)
        )
