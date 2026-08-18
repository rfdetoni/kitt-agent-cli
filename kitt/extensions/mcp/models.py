"""Domain models and configuration schemas for Model Context Protocol (MCP)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional


class MCPServerState(Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    STOPPING = "STOPPING"


@dataclass
class MCPServerConfig:
    server_id: str
    transport: str = "stdio"  # "stdio", "http", "inprocess"
    command: Optional[str] = None
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    url: Optional[str] = None
    enabled: bool = True
    trust: str = "restricted"  # "trusted", "restricted", "isolated"
    allow_tools: Optional[List[str]] = None
    deny_tools: List[str] = field(default_factory=list)
    timeout_seconds: float = 30.0
    max_output_bytes: int = 1024 * 1024  # 1 MB bounds limit


@dataclass
class MCPTool:
    server_id: str
    name: str
    description: str = ""
    input_schema: Mapping[str, Any] = field(default_factory=dict)

    @property
    def full_name(self) -> str:
        return f"mcp.{self.server_id}.{self.name}"


@dataclass
class MCPResource:
    server_id: str
    uri: str
    name: str = ""
    description: str = ""
    mime_type: Optional[str] = None


@dataclass
class MCPPrompt:
    server_id: str
    name: str
    description: str = ""
    arguments: List[Dict[str, Any]] = field(default_factory=list)
