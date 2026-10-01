from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any

from .models import SessionEventRecord

try:
    from kitt_protocol import AgentEvent
except Exception:  # protocol is optional for low-level isolated tests
    AgentEvent = None
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
            source=row["source"],
            durability=row["durability"],
            parent_event_id=row["parent_event_id"],
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
        source: str = "kitt-agent-cli",
        durability: str = "DURABLE",
        parent_event_id: str | None = None,
        event_id: str | None = None,
    ) -> SessionEventRecord:
        if not conversation_id:
            raise ValueError("conversation_id is required")
        body = dict(payload or {})
        encoded = _canonical(body)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        now = time.time()
        event_id = str(event_id or f"sev_{uuid.uuid4().hex}")
        with self.db.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prior = conn.execute(
                "SELECT * FROM session_events WHERE id=?",
                (event_id,),
            ).fetchone()
            if prior is not None:
                record = self._row(prior)
                if (
                    record.conversation_id != conversation_id
                    or record.turn_id != turn_id
                    or record.event_type != event_type
                    or record.payload_hash != digest
                ):
                    raise ValueError(f"event_id collision: {event_id}")
                conn.rollback()
                return record
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
                       parent_event_id,source,durability,payload_json,payload_hash,
                       model_visible,replayable,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event_id,
                    conversation_id,
                    turn_id,
                    episode_id,
                    sequence,
                    event_type,
                    parent_event_id,
                    str(source or "kitt-agent-cli"),
                    str(durability or "DURABLE"),
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
            source=str(source or "kitt-agent-cli"),
            durability=str(durability or "DURABLE"),
            parent_event_id=parent_event_id,
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
            source="model-gateway",
            durability="DURABLE",
        )

    def append_event(
        self,
        conversation_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        turn_id: str | None = None,
        source: str = "kitt-agent-cli",
        durability: str = "DURABLE",
        parent_event_id: str | None = None,
        model_visible: bool = False,
        replayable: bool = True,
        publisher=None,
        event_id: str | None = None,
    ) -> SessionEventRecord:
        """Persist an event before publishing it to an external observer."""
        record = self.append(
            conversation_id,
            event_type,
            payload,
            turn_id=turn_id,
            source=source,
            durability=durability,
            parent_event_id=parent_event_id,
            model_visible=model_visible,
            replayable=replayable,
            event_id=event_id,
        )
        if publisher is not None:
            publisher(event_type, dict(record.payload))
        return record

    def event_by_id(self, event_id: str) -> SessionEventRecord | None:
        if not str(event_id or "").strip():
            return None
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM session_events WHERE id=?",
                (str(event_id),),
            ).fetchone()
        return self._row(row) if row is not None else None

    @staticmethod
    def _execution_event_id(prefix: str, execution_id: str) -> str:
        digest = hashlib.sha256(str(execution_id).encode("utf-8")).hexdigest()[:40]
        return f"sev_exec_{prefix}_{digest}"

    def tool_execution(self, execution_id: str) -> dict[str, Any] | None:
        completed = self.event_by_id(self._execution_event_id("done", execution_id))
        if completed is not None:
            return {"state": "COMPLETED", "event": completed}
        reserved = self.event_by_id(self._execution_event_id("reserved", execution_id))
        if reserved is not None:
            return {"state": "RESERVED", "event": reserved}
        return None

    def reserve_tool_execution(
        self,
        conversation_id: str,
        turn_id: str,
        *,
        execution_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments_digest: str,
        side_effecting: bool,
    ) -> dict[str, Any]:
        existing = self.tool_execution(execution_id)
        if existing is not None:
            return existing
        record = self.append_event(
            conversation_id,
            "ToolExecutionReserved",
            {
                "execution_id": execution_id,
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "arguments_digest": arguments_digest,
                "side_effecting": bool(side_effecting),
            },
            turn_id=turn_id,
            source="tool-execution",
            durability="SYNC",
            replayable=True,
            event_id=self._execution_event_id("reserved", execution_id),
        )
        self.flush()
        return {"state": "RESERVED", "event": record, "fresh": True}

    def as_agent_event(self, record: SessionEventRecord):
        if AgentEvent is None:
            return {
                "event_id": record.id,
                "conversation_id": record.conversation_id,
                "turn_id": record.turn_id or "",
                "parent_event_id": record.parent_event_id,
                "seq": record.sequence,
                "kind": record.event_type,
                "source": record.source,
                "timestamp": int(record.created_at * 1000),
                "payload": dict(record.payload),
                "durability": record.durability,
            }
        return AgentEvent(
            event_id=record.id,
            conversation_id=record.conversation_id,
            turn_id=record.turn_id or "",
            parent_event_id=record.parent_event_id,
            seq=record.sequence,
            kind=record.event_type,
            source=record.source,
            timestamp=int(record.created_at * 1000),
            payload=dict(record.payload),
            durability=record.durability,
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


class EventLedger(SessionLedger):
    """Canonical name for the durable append-only execution event log."""

