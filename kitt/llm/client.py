"""Native Python HTTP client for Ollama, OpenAI, Anthropic, Gemini and OpenAI-compatible APIs."""
from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import urllib.request
from typing import Dict, Generator, List, Optional

from kitt.domain.entities import ModelProfile
from kitt.llm.auth import ProviderAuthService
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

# Backward-compatible exception aliases
LLMError = ProviderError
LLMConnectionError = ProviderConnectionError
LLMTimeoutError = ProviderTimeoutError
LLMProtocolError = ProviderProtocolError


class LLMEmptyResponseError(LLMError):
    """Raised when response is empty."""


class UnsupportedProviderError(LLMError):
    """Raised when backend provider is unknown or unsupported."""


def _with_retry(fn, max_retries: int = 3, base_delay: float = 0.5):
    """Backward-compatible helper delegating to RetryPolicy."""
    policy = RetryPolicy(RetryConfig(max_retries=max_retries, base_delay_ms=int(base_delay * 1000)))
    yield from policy.execute_with_retry(fn)


class LLMClient:
    """Unified client delegating to protocol adapters with runtime credential resolution."""

    def __init__(
        self,
        profile: ModelProfile,
        executor=None,
        retry_policy: Optional[RetryPolicy] = None,
        registry: Optional[ProviderRegistry] = None,
        auth_service: Optional[ProviderAuthService] = None,
    ):
        self.profile = profile
        self._external_executor = executor is not None
        self._executor = executor or concurrent.futures.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="llm_client_worker"
        )
        self.retry_policy = retry_policy or RetryPolicy()
        self.registry = registry or ProviderRegistry()
        self.auth_service = auth_service or self.registry.auth_service

    def close(self):
        """Shut down owned thread pool executor."""
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
        full_text = ""
        for chunk in self.chat_stream(messages, system_prompt=system_prompt, response_format=response_format):
            full_text += chunk
        if not full_text.strip():
            raise LLMEmptyResponseError("LLM returned empty response.")
        return full_text

    async def achat_stream(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        response_format: Optional[str] = None,
    ):
        queue = asyncio.Queue(maxsize=128)
        loop = asyncio.get_running_loop()
        sentinel = object()
        stop = threading.Event()

        def worker():
            try:
                for chunk in self.chat_stream(messages, system_prompt, response_format):
                    if stop.is_set():
                        break
                    # Blocks worker thread if queue is full (backpressure)
                    asyncio.run_coroutine_threadsafe(queue.put(("data", chunk)), loop).result(timeout=10.0)
                asyncio.run_coroutine_threadsafe(queue.put(("done", sentinel)), loop)
            except Exception as e:
                try:
                    asyncio.run_coroutine_threadsafe(queue.put(("error", e)), loop)
                except RuntimeError:
                    pass  # loop closed

        future = self._executor.submit(worker)

        try:
            while True:
                msg_type, payload = await queue.get()
                if msg_type == "done":
                    break
                elif msg_type == "error":
                    raise payload
                elif msg_type == "data":
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
        # 1. Resolve secret at runtime
        backend = (self.profile.backend or "").lower()
        api_key = self.auth_service.resolve(self.profile.credential_ref, provider_id=backend) or self.profile.api_key

        # 2. Resolve protocol adapter
        if self.profile.protocol:
            adapter = self.registry.get_adapter_for_protocol(self.profile.protocol)
        elif backend:
            adapter = self.registry.get_adapter_for_provider(backend)
        else:
            adapter = self.registry.get_adapter_for_protocol("openai-chat-completions")

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

        yield from self.retry_policy.execute_with_retry(lambda: adapter.stream(request))
