"""Regression for provider browser gateway capability delegation."""
from types import SimpleNamespace

import pytest

from kitt.llm.failover import FailoverLLMClient, ProviderCircuitPool
from kitt.llm.domain import ProviderRecoverableError


def test_browser_gateway_is_delegated_only_to_selected_primary():
    primary = SimpleNamespace(backend="kitt-reverse-proxy", base_url="http://127.0.0.1:3000", model="web")
    fallback = SimpleNamespace(backend="kitt-reverse-proxy", base_url="http://127.0.0.1:3001", model="web")
    called = []
    gateway = object()

    class FakeClient:
        def __init__(self, profile):
            self.profile = profile

        def create_kitt_proxy_browser_gateway(self, session_key=None, *, origin_scope=()):
            called.append((self.profile, session_key, origin_scope))
            return gateway

    wrapper = FailoverLLMClient(object(), primary, [fallback], client_factory=FakeClient)
    assert wrapper.create_kitt_proxy_browser_gateway("session", origin_scope=("https://example.org",)) is gateway
    assert called == [(primary, "session", ("https://example.org",))]
    wrapper.close()


def test_missing_browser_gateway_capability_returns_none():
    primary = SimpleNamespace(backend="ollama", base_url="http://127.0.0.1:11434", model="local")
    wrapper = FailoverLLMClient(object(), primary, client_factory=lambda _: object())
    assert wrapper.create_kitt_proxy_browser_gateway("session", origin_scope=("https://example.org",)) is None

def test_empty_model_action_falls_back_only_to_configured_trusted_profile():
    primary = SimpleNamespace(backend="kitt-reverse-proxy", base_url="http://127.0.0.1:3000", model="gemini-web")
    fallback = SimpleNamespace(backend="kitt-reverse-proxy", base_url="http://127.0.0.1:3001", model="chatgpt-web")
    calls = []

    class Client:
        def __init__(self, profile):
            self.profile = profile
            self.endpoint_policy = SimpleNamespace(is_trusted=lambda *_args: True)

        def chat_stream(self, *_args, **_kwargs):
            calls.append(self.profile.model)
            if self.profile is primary:
                raise ProviderRecoverableError(
                    "KITT reverse proxy: agent_contract_empty_action: no host tool was executed",
                    code="agent_contract_empty_action",
                )
            yield "valid host action"

    pool = ProviderCircuitPool()
    with pool.wrap(primary, [fallback], client_factory=Client) as wrapper:
        assert list(wrapper.chat_stream([{"role": "user", "content": "inspect"}])) == ["valid host action"]
    assert calls == ["gemini-web", "chatgpt-web"]


def test_no_action_is_never_blindly_replayed_on_same_provider():
    primary = SimpleNamespace(backend="kitt-reverse-proxy", base_url="http://127.0.0.1:3000", model="gemini-web")
    calls = []

    class Client:
        def __init__(self, profile):
            self.profile = profile

        def chat_stream(self, *_args, **_kwargs):
            calls.append("submitted")
            raise ProviderRecoverableError(
                "empty action", code="agent_contract_empty_action"
            )
            yield "unreachable"

    with ProviderCircuitPool().wrap(primary, [], client_factory=Client) as wrapper:
        with pytest.raises(ProviderRecoverableError):
            list(wrapper.chat_stream([{"role": "user", "content": "inspect"}]))
    assert calls == ["submitted"]


def test_no_action_after_partial_stream_does_not_fail_over():
    primary = SimpleNamespace(backend="kitt-reverse-proxy", base_url="http://127.0.0.1:3000", model="gemini-web")
    fallback = SimpleNamespace(backend="kitt-reverse-proxy", base_url="http://127.0.0.1:3001", model="chatgpt-web")
    calls = []

    class Client:
        def __init__(self, profile):
            self.profile = profile
            self.endpoint_policy = SimpleNamespace(is_trusted=lambda *_args: True)

        def chat_stream(self, *_args, **_kwargs):
            calls.append(self.profile.model)
            yield "partial"
            raise ProviderRecoverableError(
                "empty action", code="agent_contract_empty_action"
            )

    with ProviderCircuitPool().wrap(primary, [fallback], client_factory=Client) as wrapper:
        iterator = wrapper.chat_stream([{"role": "user", "content": "inspect"}])
        assert next(iterator) == "partial"
        with pytest.raises(ProviderRecoverableError):
            list(iterator)
    assert calls == ["gemini-web"]
