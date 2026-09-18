from __future__ import annotations

from kitt.domain.entities import ModelProfile
from kitt.llm.client import LLMClient
from kitt.llm.kitt_proxy_capabilities import KittProxyCapabilities, _capabilities_url, _parse


def test_capability_parser_never_exposes_proxy_reasoning_control():
    payload = {
        "kitt_agent_cli": {
            "agent_contract": {
                "version": "v1",
                "header": "X-Kitt-Agent-Contract",
                "route_header": "X-Kitt-Route",
                "routes": ["context-gather", "summarize", "code-generation", "code-edit", "validate-diff", "chat"],
            },
            "session_header": "X-Kitt-Session-Id",
            "request_id_header": "X-Kitt-Request-Id",
            "reasoning_header": "X-Kitt-Reasoning-Effort",
            "reasoning_range": [0, 100],
            "reasoning_supported": True,
            "reasoning": {
                "supported": True,
                "header": "X-Kitt-Reasoning-Effort",
                "range": [0, 100],
            },
            "session_management": {
                "version": 1,
                "provider": "chatgpt",
                "header": "X-Kitt-Session-Id",
                "max": 4,
                "idle_timeout_ms": 120000,
                "accepts_named_sessions": True,
            },
        }
    }
    parsed = _parse(payload)
    assert parsed.discovered is True
    assert parsed.provider == "chatgpt"
    assert parsed.agent_contract_version == "v1"
    assert parsed.agent_contract_header == "X-Kitt-Agent-Contract"
    assert parsed.agent_route_header == "X-Kitt-Route"
    assert "code-edit" in parsed.agent_routes
    assert parsed.reasoning_supported is False
    assert parsed.reasoning_header is None
    assert parsed.max_sessions == 4
    assert parsed.idle_timeout_ms == 120000


def test_capabilities_url_accepts_root_v1_and_chat_endpoints():
    assert _capabilities_url("http://127.0.0.1:3000") == "http://127.0.0.1:3000/v1/capabilities"
    assert _capabilities_url("http://127.0.0.1:3000/v1") == "http://127.0.0.1:3000/v1/capabilities"
    assert _capabilities_url("http://127.0.0.1:3000/v1/chat/completions") == "http://127.0.0.1:3000/v1/capabilities"


def test_llm_client_uses_session_headers_but_never_reasoning_header(monkeypatch):
    captured = []

    class Adapter:
        def stream(self, request):
            captured.append(request)
            yield "ok"

    adapter = Adapter()

    class Registry:
        auth_service = object()

        def get_adapter_for_protocol(self, _protocol):
            return adapter

        def get_adapter_for_provider(self, _provider):
            return adapter

    monkeypatch.setattr(
        "kitt.llm.client.resolve_endpoint_credential",
        lambda *args, **kwargs: None,
    )
    # Even a stale/older proxy advertising reasoning control must not make the
    # CLI send a reasoning header. WebChat remains authoritative.
    monkeypatch.setattr(
        "kitt.llm.client.discover_kitt_proxy_capabilities",
        lambda *args, **kwargs: KittProxyCapabilities(
            discovered=True,
            agent_contract_version="v1",
            agent_contract_header="X-Test-Agent-Contract",
            agent_route_header="X-Test-Route",
            agent_routes=("context-gather", "summarize", "code-generation", "code-edit", "validate-diff", "chat"),
            session_header="X-Test-Session",
            request_id_header="X-Test-Request",
            reasoning_header="X-Test-Reasoning",
            reasoning_supported=True,
            reasoning_range=(10, 80),
            session_management={
                "provider": "chatgpt",
                "accepts_named_sessions": True,
                "max": 3,
            },
        ),
    )

    profile = ModelProfile(
        backend="kitt-reverse-proxy",
        model="provider-model-without-client-hardcode",
        base_url="http://127.0.0.1:3000",
        protocol="kitt-reverse-proxy",
    )
    client = LLMClient(
        profile,
        registry=Registry(),
        auth_service=object(),
        endpoint_policy=object(),
    )
    try:
        assert "".join(
            client.chat_stream(
                [{"role": "user", "content": "hello"}],
                session_key="conversation-1",
                reasoning_effort=95,
            )
        ) == "ok"
    finally:
        client.close()

    headers = captured[0].extra_headers
    assert headers["X-Test-Agent-Contract"] == "v1"
    assert headers["X-Test-Route"] == "chat"
    assert "X-Test-Session" in headers
    assert "X-Test-Request" in headers
    assert "X-Test-Reasoning" not in headers
    assert "X-Kitt-Reasoning-Effort" not in headers
    assert client.kitt_proxy_capabilities is not None
    assert client.kitt_proxy_capabilities.max_sessions == 3


def test_llm_client_does_not_fallback_to_legacy_reasoning_header_when_discovery_fails(monkeypatch):
    captured = []

    class Adapter:
        def stream(self, request):
            captured.append(request)
            yield "ok"

    adapter = Adapter()

    class Registry:
        auth_service = object()

        def get_adapter_for_protocol(self, _protocol):
            return adapter

    monkeypatch.setattr("kitt.llm.client.resolve_endpoint_credential", lambda *a, **k: None)
    monkeypatch.setattr(
        "kitt.llm.client.discover_kitt_proxy_capabilities",
        lambda *a, **k: KittProxyCapabilities(discovered=False),
    )

    client = LLMClient(
        ModelProfile(
            backend="kitt-reverse-proxy",
            model="chatgpt-web",
            base_url="http://127.0.0.1:3000",
            protocol="kitt-reverse-proxy",
        ),
        registry=Registry(),
        auth_service=object(),
        endpoint_policy=object(),
    )
    try:
        list(client.chat_stream([{"role": "user", "content": "hello"}], reasoning_effort=90))
    finally:
        client.close()

    assert "X-Kitt-Reasoning-Effort" not in captured[0].extra_headers



def test_llm_client_fails_closed_on_discovered_incompatible_agent_contract(monkeypatch):
    class Adapter:
        def stream(self, request):
            yield "should-not-run"

    class Registry:
        auth_service = object()

        def get_adapter_for_protocol(self, _protocol):
            return Adapter()

    monkeypatch.setattr("kitt.llm.client.resolve_endpoint_credential", lambda *a, **k: None)
    monkeypatch.setattr(
        "kitt.llm.client.discover_kitt_proxy_capabilities",
        lambda *a, **k: KittProxyCapabilities(
            discovered=True,
            agent_contract_version="v2",
            agent_contract_header="X-Kitt-Agent-Contract",
            agent_route_header="X-Kitt-Route",
            agent_routes=("chat",),
        ),
    )

    client = LLMClient(
        ModelProfile(
            backend="kitt-reverse-proxy",
            model="chatgpt-web",
            base_url="http://127.0.0.1:3000",
            protocol="kitt-reverse-proxy",
        ),
        registry=Registry(),
        auth_service=object(),
        endpoint_policy=object(),
    )
    try:
        import pytest

        with pytest.raises(Exception, match="agent contract is incompatible"):
            list(client.chat_stream([{"role": "user", "content": "hello"}]))
    finally:
        client.close()
