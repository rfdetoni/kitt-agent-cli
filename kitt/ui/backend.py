from __future__ import annotations

from typing import Protocol

from kitt.ui.app import KittUIApp


class UIBackend(Protocol):
    async def run_async(self) -> int: ...
    async def shutdown(self) -> None: ...


# Import-safe public name expected by callers; prompt_toolkit remains imported
# lazily by KittUIApp only when controls/application are built.
PromptToolkitBackend = KittUIApp

__all__ = ["UIBackend", "PromptToolkitBackend"]
