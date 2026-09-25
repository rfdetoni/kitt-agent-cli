from __future__ import annotations

import hashlib
import json
from typing import Any

from .ledger import SessionLedger


class SessionReplayService:
    """Deterministic, keyless replay helpers over durable session events."""

    def __init__(self, ledger: SessionLedger):
        self.ledger = ledger

    def model_requests(
        self,
        conversation_id: str,
        *,
        turn_id: str | None = None,
    ) -> list[dict[str, Any]]:
        result = []
        for event in self.ledger.events(conversation_id, turn_id=turn_id, limit=10000):
            if event.event_type != "ModelRequestPrepared":
                continue
            result.append(
                {
                    "sequence": event.sequence,
                    "payload_hash": event.payload_hash,
                    **event.payload,
                }
            )
        return result

    def fingerprint(self, conversation_id: str, *, turn_id: str | None = None) -> str:
        requests = self.model_requests(conversation_id, turn_id=turn_id)
        encoded = json.dumps(
            requests,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def matches(
        self,
        conversation_id: str,
        expected_fingerprint: str,
        *,
        turn_id: str | None = None,
    ) -> bool:
        return self.fingerprint(conversation_id, turn_id=turn_id) == str(expected_fingerprint)
