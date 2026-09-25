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


    def save_golden(
        self,
        conversation_id: str,
        name: str,
        *,
        turn_id: str | None = None,
    ) -> dict[str, Any]:
        clean_name = str(name or "").strip()
        if not clean_name:
            raise ValueError("golden replay name is required")
        requests = self.model_requests(conversation_id, turn_id=turn_id)
        fingerprint = self.fingerprint(conversation_id, turn_id=turn_id)
        golden_key = f"{conversation_id}:{turn_id or ''}:{clean_name}"
        golden_id = f"gold_{hashlib.sha256(golden_key.encode('utf-8')).hexdigest()[:32]}"
        payload = {
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "requests": requests,
        }
        with self.ledger.db.get_connection() as conn:
            conn.execute(
                """INSERT INTO session_replay_goldens(
                       id,conversation_id,turn_id,name,fingerprint,
                       request_count,payload_json,created_at
                   ) VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(conversation_id,name)
                   DO UPDATE SET turn_id=excluded.turn_id,
                       fingerprint=excluded.fingerprint,
                       request_count=excluded.request_count,
                       payload_json=excluded.payload_json,
                       created_at=excluded.created_at""",
                (
                    golden_id,
                    conversation_id,
                    turn_id,
                    clean_name,
                    fingerprint,
                    len(requests),
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        default=str,
                    ),
                    __import__("time").time(),
                ),
            )
        return {
            "id": golden_id,
            "name": clean_name,
            "fingerprint": fingerprint,
            "request_count": len(requests),
        }

    def verify_golden(
        self,
        conversation_id: str,
        name: str,
    ) -> dict[str, Any]:
        with self.ledger.db.get_connection() as conn:
            row = conn.execute(
                """SELECT * FROM session_replay_goldens
                   WHERE conversation_id=? AND name=?""",
                (conversation_id, str(name)),
            ).fetchone()
        if not row:
            raise ValueError("golden replay not found")
        turn_id = row["turn_id"]
        actual = self.fingerprint(conversation_id, turn_id=turn_id)
        expected = str(row["fingerprint"])
        return {
            "id": row["id"],
            "name": row["name"],
            "turn_id": turn_id,
            "expected_fingerprint": expected,
            "actual_fingerprint": actual,
            "matches": actual == expected,
            "expected_request_count": int(row["request_count"]),
            "actual_request_count": len(
                self.model_requests(conversation_id, turn_id=turn_id)
            ),
        }
