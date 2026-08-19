from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class DaemonEvent:
    sequence_id: int
    session_id: str
    event_type: str
    payload: Dict[str, Any]
    created_at: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sequence_id": self.sequence_id,
            "session_id": self.session_id,
            "event_type": self.event_type,
            "payload": self.payload,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DaemonEvent":
        return cls(
            sequence_id=data["sequence_id"],
            session_id=data["session_id"],
            event_type=data["event_type"],
            payload=data.get("payload", {}),
            created_at=data["created_at"],
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
