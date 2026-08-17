"""Tool execution handlers implementing Strategy pattern."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from kitt.tools.registry import ToolRegistry, ToolResult


@dataclass(frozen=True)
class ToolContext:
    registry: "ToolRegistry"
    turn_id: str
    conversation_id: str
    workspace_id: str
    origin: str


class ToolHandler(Protocol):
    def execute(self, args: Dict[str, Any], ctx: ToolContext) -> "ToolResult":
        ...
