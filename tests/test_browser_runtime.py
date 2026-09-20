from __future__ import annotations

import base64
import json

from kitt.llm.browser_gateway import KittProxyBrowserGateway
from kitt.llm.kitt_proxy_capabilities import _parse
from kitt.runtime.core_runtime import OPERATION_SPECS
from kitt.security.capabilities import (
    CAP_BROWSER_READ,
    CAP_BROWSER_WRITE,
    READ_ONLY_CAPABILITIES,
)


class _Response:
    def __init__(self, payload: dict):
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._payload


def test_proxy_capabilities_parse_browser_contract():
    parsed = _parse({
        "kitt_agent_cli": {
            "session_header": "X-Kitt-Session-Id",
            "session_management": {"accepts_named_sessions": True},
            "browser_automation": {
                "supported": True,
                "actions": ["open", "inspect", "screenshot", "click", "type", "close"],
            },
        }
    })
    assert parsed.browser_supported is True
    assert parsed.browser_actions == (
        "open", "inspect", "screenshot", "click", "type", "close"
    )


def test_browser_runtime_capabilities_are_separate():
    assert OPERATION_SPECS["browser.open"].required_capability == CAP_BROWSER_READ
    assert OPERATION_SPECS["browser.inspect"].required_capability == CAP_BROWSER_READ
    assert OPERATION_SPECS["browser.screenshot"].required_capability == CAP_BROWSER_READ
    assert OPERATION_SPECS["browser.click"].required_capability == CAP_BROWSER_WRITE
    assert OPERATION_SPECS["browser.type"].required_capability == CAP_BROWSER_WRITE
    assert OPERATION_SPECS["browser.close"].required_capability == CAP_BROWSER_WRITE
    assert CAP_BROWSER_READ in READ_ONLY_CAPABILITIES
    assert CAP_BROWSER_WRITE not in READ_ONLY_CAPABILITIES


def test_gateway_strips_screenshot_base64_and_queues_image(monkeypatch):
    raw = b"visual-bytes"
    captured = {}

    def fake_open(request, **_kwargs):
        captured["url"] = request.full_url
        captured["session"] = request.get_header("X-kitt-session-id")
        return _Response({
            "action": "screenshot",
            "format": "jpeg",
            "bytes": len(raw),
            "image_base64": base64.b64encode(raw).decode("ascii"),
            "url": "http://localhost:4200/",
        })

    monkeypatch.setattr("kitt.llm.browser_gateway.secure_urlopen", fake_open)
    gateway = KittProxyBrowserGateway(
        base_url="http://127.0.0.1:3000/v1",
        api_key=None,
        session_header="X-Kitt-Session-Id",
        session_id="abc123",
        request_id_header="X-Kitt-Request-Id",
        allowed_actions=("screenshot",),
    )

    result = gateway.execute("screenshot", {"format": "jpeg"})
    assert "image_base64" not in result
    assert result["image_attached"] is True
    assert captured["url"].endswith("/v1/kitt/browser/screenshot")
    assert captured["session"] == "abc123"
    images = gateway.drain_images()
    assert len(images) == 1
    assert images[0].data == raw
    assert gateway.drain_images() == ()
