from __future__ import annotations

from kitt.domain.entities import ModelProfile
from kitt.llm.client import LLMClient
from kitt.llm.kitt_proxy_capabilities import KittProxyCapabilities, _capabilities_url, _parse


def test_capability_parser_derives_reasoning_from_advertised_provider():
    payload = {
        "kitt_agent_cli": {
            "session_header": "X-Kitt-Session-Id",
            "request_id_header": "X-Kitt-Request-Id",
            "reasoning_header": "X-Kitt-Reasoning-Effort",
            "reasoning_range": [0, 100],
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
    assert parsed.reasoning_supported is True
    assert parsed.max_sessions == 4
    assert parsed.idle_timeout_ms == 120000

    payload["kitt_agent_cli"]["session_management"]["provider"] = "claude"
    assert _parse(payload).reasoning_supported is False


def test_capabilities_url_accepts_root_v1_and_chat_endpoints():
    assert _capabilities_url("http://127.0.0.1:3000") == "http://127.0.0.1:3000/v1/capabilities"
    assert _capabilities_url("http://127.0.0.1:3000/v1") == "http://127.0.0.1:3000/v1/capabilities"
    assert _capabilities_url("http://127.0.0.1:3000/v1/chat/completions") == "http://127.0.0.1:3000/v1/capabilities"


def test_llm_client_uses_advertised_headers_and_reasoning_support(monkeypatch):
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
    monkeypatch.setattr(
        "kitt.llm.client.discover_kitt_proxy_capabilities",
        lambda *args, **kwargs: KittProxyCapabilities(
            discovered=True,
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
    assert "X-Test-Session" in headers
    assert "X-Test-Request" in headers
    assert headers["X-Test-Reasoning"] == "80"
    assert "X-Kitt-Session-Id" not in headers
    assert client.kitt_proxy_capabilities is not None
    assert client.kitt_proxy_capabilities.max_sessions == 3


def test_llm_client_suppresses_reasoning_when_provider_does_not_support_it(monkeypatch):
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
        lambda *a, **k: KittProxyCapabilities(
            discovered=True,
            session_header="X-Kitt-Session-Id",
            request_id_header="X-Kitt-Request-Id",
            reasoning_header="X-Kitt-Reasoning-Effort",
            reasoning_supported=False,
            session_management={"provider": "claude", "accepts_named_sessions": True},
        ),
    )

    client = LLMClient(
        ModelProfile(
            backend="kitt-reverse-proxy",
            model="claude-web",
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
