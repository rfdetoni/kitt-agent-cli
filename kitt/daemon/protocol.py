from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


DAEMON_PROTOCOL_VERSION = 1


@dataclass(frozen=True)
class DaemonEvent:
    sequence_id: int
    session_id: str
    event_type: str
    payload: Dict[str, Any]
    created_at: float
    protocol_version: int = DAEMON_PROTOCOL_VERSION
    workspace_id: str = ""
    conversation_id: str = ""
    turn_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sequence_id": self.sequence_id,
            "session_id": self.session_id,
            "event_type": self.event_type,
            "payload": self.payload,
            "created_at": self.created_at,
            "protocol_version": self.protocol_version,
            "workspace_id": self.workspace_id or self.payload.get("workspace_id", ""),
            "conversation_id": self.conversation_id or self.session_id,
            "turn_id": self.turn_id or self.payload.get("turn_id", ""),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DaemonEvent":
        return cls(
            sequence_id=data["sequence_id"],
            session_id=data["session_id"],
            event_type=data["event_type"],
            payload=data.get("payload", {}),
            created_at=data["created_at"],
            protocol_version=data.get("protocol_version", DAEMON_PROTOCOL_VERSION),
            workspace_id=data.get("workspace_id", ""),
            conversation_id=data.get("conversation_id", data.get("session_id", "")),
            turn_id=data.get("turn_id", ""),
        )


def encode_message(msg: Dict[str, Any]) -> bytes:
    """Encode message payload as newline-delimited UTF-8 JSON."""
    line = json.dumps(msg, ensure_ascii=False) + "\n"
    return line.encode("utf-8")


def decode_line(line: bytes | str) -> Dict[str, Any]:
    """Decode a single line of UTF-8 JSON."""
    if isinstance(line, bytes):
        line = line.decode("utf-8", errors="replace")
    return json.loads(line.strip())
