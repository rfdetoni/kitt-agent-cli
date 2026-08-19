"""Tool execution handlers implementing Strategy pattern."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from kitt.tools.registry import ToolRegistry, ToolResult
    from kitt.security.context import ExecutionSecurityContext


@dataclass(frozen=True)
class ToolContext:
    registry: "ToolRegistry"
    turn_id: str
    conversation_id: str
    workspace_id: str
    origin: str
    security_context: Optional["ExecutionSecurityContext"] = None
    approval_grant: Any = None
    expected_approval_id: Optional[str] = None


class ToolHandler(Protocol):
    def execute(self, args: Dict[str, Any], ctx: ToolContext) -> "ToolResult":
        ...
