from __future__ import annotations

import asyncio

from kitt.reverse_proxy.bindings import bind_instance_to_role
from kitt.reverse_proxy.contracts import ReverseProxyInstance


class _FakeUI:
    def __init__(self):
        self.calls = []

    async def _set_model_role(self, role, model, provider, base_url):
        self.calls.append((role, model, provider, base_url))


def _instance(instance_id, provider, model, port):
    return ReverseProxyInstance(
        id=instance_id,
        provider=provider,
        model=model,
        target=provider,
        profile_id=f"{provider}-profile",
        host="127.0.0.1",
        port=port,
        pid=1000 + port,
        status="ready",
        started_at="2026-09-25T00:00:00Z",
    )


def test_gemini_context_and_chatgpt_code_bind_to_independent_proxy_endpoints():
    ui = _FakeUI()
    gemini = _instance("gemini-context", "gemini", "gemini-web", 3000)
    chatgpt = _instance("chatgpt-code", "chatgpt", "chatgpt-web", 3001)

    asyncio.run(bind_instance_to_role(ui, "context", gemini))
    asyncio.run(bind_instance_to_role(ui, "code", chatgpt))

    assert ui.calls == [
        ("context", "gemini-web", "kitt-reverse-proxy", "http://127.0.0.1:3000"),
        ("principal", "chatgpt-web", "kitt-reverse-proxy", "http://127.0.0.1:3001"),
    ]


def test_instance_endpoint_is_derived_without_exposing_process_details_to_router():
    instance = _instance("gemini-context", "gemini", "gemini-web", 3007)
    assert instance.endpoint == "http://127.0.0.1:3007"
