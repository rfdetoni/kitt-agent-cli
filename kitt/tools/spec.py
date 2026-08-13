"""Typed tool specifications and validation schemas."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any, Tuple, Optional


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]
    effects: Tuple[str, ...]  # read|write|execute|network
    default_timeout_ms: int = 10000
    max_output_bytes: int = 100000
    approval_policy: str = "AUTO"  # AUTO|ASK|DENY

    def validate_args(self, args: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Validate incoming arguments against required keys in input_schema."""
        required = self.input_schema.get("required", [])
        for req in required:
            if req not in args:
                return False, f"Missing required parameter '{req}' for tool '{self.name}'"
        return True, None
