from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass

from kitt.llm.client import LLMClient
from kitt.llm.retry import RetryPolicy


@dataclass
class _ProviderState:
    failures: int = 0
    parked_until: float = 0.0
    last_error: str = ""


def _identity(profile) -> tuple[str, str, str]:
    return (
        str(getattr(profile, "backend", "") or "").strip().lower(),
        str(getattr(profile, "base_url", "") or "").strip().rstrip("/").lower(),
        str(getattr(profile, "model", "") or "").strip(),
    )


class ProviderCircuitPool:
    """Bounded provider parking and fallback over existing model profiles."""

    def __init__(self, park_seconds: float = 30.0):
        self.park_seconds = max(1.0, float(park_seconds))
        self._states: dict[tuple[str, str, str], _ProviderState] = {}
        self._lock = threading.RLock()
        self._retry = RetryPolicy()

    def state(self, profile):
        with self._lock:
            value = self._states.get(_identity(profile), _ProviderState())
            return {
                "failures": value.failures,
                "parked_until": value.parked_until,
                "last_error": value.last_error,
            }

    def mark_success(self, profile):
        with self._lock:
            self._states[_identity(profile)] = _ProviderState()

    def mark_failure(self, profile, exc: Exception):
        key = _identity(profile)
        with self._lock:
            state = self._states.setdefault(key, _ProviderState())
            state.failures += 1
            multiplier = min(8, 2 ** max(0, state.failures - 1))
            state.parked_until = time.monotonic() + self.park_seconds * multiplier
            state.last_error = f"{type(exc).__name__}: {exc}"

    def retryable(self, exc: Exception) -> bool:
        return self._retry.is_retryable(exc)

    def candidates(self, primary, alternatives):
        unique = []
        seen = set()
        for profile in [primary, *list(alternatives or [])]:
            key = _identity(profile)
            if key in seen:
                continue
            seen.add(key)
            unique.append(profile)

        now = time.monotonic()
        with self._lock:
            ready = [
                profile
                for profile in unique
                if self._states.get(_identity(profile), _ProviderState()).parked_until <= now
            ]
            if ready:
                return ready
            return sorted(
                unique,
                key=lambda profile: self._states.get(
                    _identity(profile), _ProviderState()
                ).parked_until,
            )[:1]

    def wrap(self, primary, alternatives=(), client_factory=LLMClient):
        return FailoverLLMClient(
            self,
            primary,
            alternatives,
            client_factory=client_factory,
        )


class FailoverLLMClient:
    """LLMClient-compatible wrapper that never replays partial visible output."""

    def __init__(self, pool, primary, alternatives=(), *, client_factory=LLMClient):
        self.pool = pool
        self.profile = primary
        self.alternatives = list(alternatives or [])
        self.client_factory = client_factory
        self._clients = {}

    def _client(self, profile):
        key = _identity(profile)
        client = self._clients.get(key)
        if client is None:
            client = self.client_factory(profile)
            self._clients[key] = client
        return client

    def _candidates(self):
        # Configured profiles are not endpoint authorization. An untrusted
        # fallback must not mask the selected provider's connection error.
        alternatives = []
        for profile in self.alternatives:
            policy = getattr(self._client(profile), "endpoint_policy", None)
            if policy is None or policy.is_trusted(profile.backend, profile.base_url):
                alternatives.append(profile)
        return self.pool.candidates(self.profile, alternatives)

    @property
    def capabilities(self):
        return getattr(self._client(self.profile), "capabilities", None)

    def chat(self, *args, **kwargs):
        last = None
        for profile in self._candidates():
            try:
                result = self._client(profile).chat(*args, **kwargs)
                self.pool.mark_success(profile)
                return result
            except Exception as exc:
                last = exc
                if not self.pool.retryable(exc):
                    raise
                self.pool.mark_failure(profile, exc)
        if last is not None:
            raise last
        raise RuntimeError("no provider candidates available")

    def chat_stream(self, *args, **kwargs):
        last = None
        for profile in self._candidates():
            emitted = False
            try:
                for chunk in self._client(profile).chat_stream(*args, **kwargs):
                    emitted = True
                    yield chunk
                self.pool.mark_success(profile)
                return
            except Exception as exc:
                last = exc
                if emitted or not self.pool.retryable(exc):
                    raise
                self.pool.mark_failure(profile, exc)
        if last is not None:
            raise last

    async def achat_stream(self, *args, **kwargs):
        last = None
        for profile in self._candidates():
            emitted = False
            try:
                client = self._client(profile)
                if hasattr(client, "achat_stream"):
                    async for chunk in client.achat_stream(*args, **kwargs):
                        emitted = True
                        yield chunk
                else:
                    chunks = await asyncio.to_thread(
                        lambda: list(client.chat_stream(*args, **kwargs))
                    )
                    for chunk in chunks:
                        emitted = True
                        yield chunk
                self.pool.mark_success(profile)
                return
            except Exception as exc:
                last = exc
                if emitted or not self.pool.retryable(exc):
                    raise
                self.pool.mark_failure(profile, exc)
        if last is not None:
            raise last

    def close(self):
        for client in self._clients.values():
            close = getattr(client, "close", None)
            if close is not None:
                close()
        self._clients.clear()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
