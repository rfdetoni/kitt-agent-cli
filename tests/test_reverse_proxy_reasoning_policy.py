from __future__ import annotations

import asyncio
from types import SimpleNamespace

from kitt.ui.commands import CommandRegistry
from kitt.ui.reasoning_policy import install_reverse_proxy_reasoning_policy


class _Router:
    def __init__(self, backend: str, protocol: str):
        self.profile = SimpleNamespace(backend=backend, protocol=protocol)

    def resolve_profile_for_task(self, _task):
        return "execution", self.profile


class _FakeApp:
    def __init__(self, backend="kitt-reverse-proxy", protocol="kitt-reverse-proxy"):
        self.runtime = SimpleNamespace(
            processor=SimpleNamespace(
                router=_Router(backend, protocol),
                reasoning_effort=50,
            )
        )
        self.commands = CommandRegistry()
        self.reasoning_updates = []

    async def _execute_command(self, raw: str) -> bool:
        found = self.commands.find(raw.split(maxsplit=1)[0])
        return found is not None

    async def _set_reasoning_effort(self, value: int, *, notify: bool = True) -> None:
        self.reasoning_updates.append((value, notify))

    def _header_text(self):
        return [
            ("class:primary", " K.I.T.T. "),
            ("class:primary", " 🧠 Reasoning: 50% (Ctrl+←/→) "),
        ]

    def _sidebar_text(self):
        return " MODELS\n chatgpt-web\n 🧠 Reasoning: 50%\n CONTEXT\n"


install_reverse_proxy_reasoning_policy(_FakeApp)


def test_reverse_proxy_hides_reasoning_command_and_displays():
    app = _FakeApp()

    assert app.commands.find("/reasoning") is None
    assert all(item.id != "reasoning" for item in app.commands.search("/"))
    assert "reasoning" not in app.commands.commands
    assert "Reasoning" not in "".join(text for _style, text in app._header_text())
    assert "Reasoning" not in app._sidebar_text()


def test_reverse_proxy_consumes_legacy_reasoning_command_without_changing_state():
    app = _FakeApp()

    assert asyncio.run(app._execute_command("/reasoning 90")) is True
    asyncio.run(app._set_reasoning_effort(90))

    assert app.reasoning_updates == []
    assert app.runtime.processor.reasoning_effort == 50


def test_api_provider_keeps_existing_reasoning_controls():
    app = _FakeApp(backend="openai", protocol="openai-chat-completions")

    assert app.commands.find("/reasoning") is not None
    assert "Reasoning" in "".join(text for _style, text in app._header_text())
    asyncio.run(app._set_reasoning_effort(80, notify=False))
    assert app.reasoning_updates == [(80, False)]
