"""Regression for provider browser gateway capability delegation."""
from types import SimpleNamespace

from kitt.llm.failover import FailoverLLMClient


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
