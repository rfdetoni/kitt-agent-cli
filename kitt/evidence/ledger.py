from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any

from .models import SessionEventRecord
from .projections import SessionProjectionRegistry, build_default_projection_registry


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(key): _jsonable(child) for key, child in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_jsonable(child) for child in value]
        return str(value)


def _canonical(value: Any) -> str:
    return json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


class SessionLedger:
    """Append-only durable events used to replay model-visible state."""

    def __init__(
        self,
        db,
        projections: SessionProjectionRegistry | None = None,
    ):
        self.db = db
        self.projections = projections or build_default_projection_registry(db)
        if self.projections.db is None:
            self.projections.attach(db)

    @staticmethod
    def _row(row) -> SessionEventRecord:
        return SessionEventRecord(
            id=row["id"],
            conversation_id=row["conversation_id"],
            turn_id=row["turn_id"],
            episode_id=row["episode_id"],
            sequence=int(row["sequence"]),
            event_type=row["event_type"],
            payload=json.loads(row["payload_json"] or "{}"),
            payload_hash=row["payload_hash"],
            model_visible=bool(row["model_visible"]),
            replayable=bool(row["replayable"]),
            created_at=float(row["created_at"]),
        )

    def append(
        self,
        conversation_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        turn_id: str | None = None,
        episode_id: str | None = None,
        model_visible: bool = False,
        replayable: bool = True,
        force_checkpoint: bool = False,
    ) -> SessionEventRecord:
        if not conversation_id:
            raise ValueError("conversation_id is required")
        body = dict(payload or {})
        encoded = _canonical(body)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        now = time.time()
        event_id = f"sev_{uuid.uuid4().hex}"
        with self.db.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            exists = conn.execute(
                "SELECT 1 FROM conversations WHERE id=?",
                (conversation_id,),
            ).fetchone()
            if not exists:
                raise ValueError("Conversation not found")
            sequence = int(
                conn.execute(
                    "SELECT COALESCE(MAX(sequence),0)+1 FROM session_events WHERE conversation_id=?",
                    (conversation_id,),
                ).fetchone()[0]
            )
            conn.execute(
                """INSERT INTO session_events(
                       id,conversation_id,turn_id,episode_id,sequence,event_type,
                       payload_json,payload_hash,model_visible,replayable,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event_id,
                    conversation_id,
                    turn_id,
                    episode_id,
                    sequence,
                    event_type,
                    encoded,
                    digest,
                    int(model_visible),
                    int(replayable),
                    now,
                ),
            )
        record = SessionEventRecord(
            id=event_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            episode_id=episode_id,
            sequence=sequence,
            event_type=event_type,
            payload=body,
            payload_hash=digest,
            model_visible=model_visible,
            replayable=replayable,
            created_at=now,
        )
        self.projections.observe(record, force_checkpoint=force_checkpoint)
        return record

    def append_model_request(
        self,
        conversation_id: str,
        turn_id: str,
        *,
        system_prompt: str,
        messages: list[dict[str, Any]],
        route: str = "",
        profile: str = "",
        model: str = "",
        episode_id: str | None = None,
    ) -> SessionEventRecord:
        payload = {
            "system_prompt": system_prompt,
            "messages": _jsonable(messages),
            "route": route,
            "profile": profile,
            "model": model,
        }
        return self.append(
            conversation_id,
            "ModelRequestPrepared",
            payload,
            turn_id=turn_id,
            episode_id=episode_id,
            model_visible=True,
            replayable=True,
            force_checkpoint=True,
        )

    def events(
        self,
        conversation_id: str,
        *,
        turn_id: str | None = None,
        after_sequence: int = 0,
        limit: int = 1000,
    ) -> list[SessionEventRecord]:
        sql = "SELECT * FROM session_events WHERE conversation_id=? AND sequence>?"
        args: list[Any] = [conversation_id, max(0, int(after_sequence))]
        if turn_id:
            sql += " AND turn_id=?"
            args.append(turn_id)
        sql += " ORDER BY sequence ASC LIMIT ?"
        args.append(max(1, min(int(limit), 10000)))
        with self.db.get_connection() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [self._row(row) for row in rows]

    def latest_model_request(
        self,
        conversation_id: str,
        turn_id: str | None = None,
    ) -> dict[str, Any] | None:
        sql = (
            "SELECT payload_json,payload_hash,sequence FROM session_events "
            "WHERE conversation_id=? AND event_type='ModelRequestPrepared'"
        )
        args: list[Any] = [conversation_id]
        if turn_id:
            sql += " AND turn_id=?"
            args.append(turn_id)
        sql += " ORDER BY sequence DESC LIMIT 1"
        with self.db.get_connection() as conn:
            row = conn.execute(sql, args).fetchone()
        if not row:
            return None
        payload = json.loads(row["payload_json"])
        payload["payload_hash"] = row["payload_hash"]
        payload["sequence"] = int(row["sequence"])
        return payload
