from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from kitt.reverse_proxy.bindings import bind_instance_to_role
from kitt.reverse_proxy.contracts import ReverseProxyInstance, ReverseProxyPlugin
from kitt.ui.interaction import InteractionMap
from kitt.ui.reverse_proxy_panel import (
    ReverseProxyPanelModel,
    _open_reverse_proxy_overlay,
    _reverse_proxy_text,
)


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


def test_reverse_proxy_overlay_opens_before_control_plane_snapshot_loads():
    async def scenario():
        events = []
        ui = SimpleNamespace(
            reverse_proxy_model=ReverseProxyPanelModel(),
            reverse_proxy_control=object(),
            reverse_proxy_client=object(),
            application=MagicMock(),
        )
        ui.open_overlay = lambda name, control: events.append(("open", name))
        ui.application.invalidate = MagicMock()

        async def run_blocking(func, *args, **kwargs):
            events.append(("load", getattr(func, "__name__", "call")))
            return ([], [], [])

        ui._run_blocking = run_blocking

        await _open_reverse_proxy_overlay(ui)

        assert events[0] == ("open", "reverse_proxy")
        assert events[1][0] == "load"
        assert ui.reverse_proxy_model.loading is False

    asyncio.run(scenario())


def test_reverse_proxy_modal_registers_clickable_start_service_action():
    model = ReverseProxyPanelModel(page="plugins")
    model.plugins = [
        ReverseProxyPlugin(
            id="gemini",
            name="Gemini Web",
            version="1",
            source="builtin",
            default_url="https://gemini.google.com/app",
            default_model="gemini-web",
            transports=("webchat",),
            auth="browser",
        )
    ]
    ui = SimpleNamespace(
        reverse_proxy_model=model,
        interactions=InteractionMap(),
    )

    text = _reverse_proxy_text(ui)
    lines = text.splitlines()
    row = next(i for i, line in enumerate(lines) if "[Enter] Iniciar serviço" in line)
    x = lines[row].index("[Enter] Iniciar serviço")
    region = ui.interactions.resolve("reverse_proxy", x, row)

    assert region is not None
    assert region.action == "reverse_proxy.start"
