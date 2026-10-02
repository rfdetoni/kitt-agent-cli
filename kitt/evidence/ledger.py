from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any
import re

from kitt.history.redaction import redact as redact_secret_text

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


_SENSITIVE_KEY = re.compile(
    r"(authorization|cookie|token|secret|password|passwd|api[-_]?key|credential|csrf|xsrf)",
    re.IGNORECASE,
)


def _redact_observability(value: Any, key: str = "") -> Any:
    if key and _SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, str):
        return redact_secret_text(value)
    if isinstance(value, dict):
        return {
            str(child_key): _redact_observability(child_value, str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_redact_observability(item) for item in value]
    return value


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
        body = _redact_observability(dict(payload or {}))
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
        # The append-only event is the authoritative durable record. Projection
        # checkpoints are rebuildable and must remain sparse so model dispatch does
        # not acquire one extra SQLite write lock per projection.
        return self.append(
            conversation_id,
            "ModelRequestPrepared",
            payload,
            turn_id=turn_id,
            episode_id=episode_id,
            model_visible=True,
            replayable=True,
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

    @staticmethod
    def _attempt_id(execution_id: str, attempt_number: int) -> str:
        digest = hashlib.sha256(
            f"{execution_id}:attempt:{max(1, int(attempt_number))}".encode("utf-8")
        ).hexdigest()[:40]
        return f"attempt_{digest}"

    def _latest_retry(self, reserved: SessionEventRecord) -> dict[str, Any] | None:
        with self.db.get_connection() as conn:
            row = conn.execute(
                """SELECT payload_json FROM session_events
                   WHERE conversation_id=? AND event_type='ToolExecutionRetry'
                     AND parent_event_id=?
                   ORDER BY sequence DESC LIMIT 1""",
                (reserved.conversation_id, reserved.id),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"] or "{}")
        return payload if isinstance(payload, dict) else None

    def tool_execution(self, execution_id: str) -> dict[str, Any] | None:
        completed = self.event_by_id(self._execution_event_id("done", execution_id))
        if completed is not None:
            attempt_number = max(1, int(completed.payload.get("attempt_number") or 1))
            return {
                "state": "COMPLETED",
                "outcome": "SUCCEEDED" if completed.payload.get("success") else "FAILED",
                "event": completed,
                "operation_id": execution_id,
                "attempt_id": str(
                    completed.payload.get("attempt_id")
                    or self._attempt_id(execution_id, attempt_number)
                ),
                "attempt_number": attempt_number,
            }
        reserved = self.event_by_id(self._execution_event_id("reserved", execution_id))
        if reserved is not None:
            retry = self._latest_retry(reserved)
            attempt_number = max(
                1,
                int(
                    (retry or {}).get("attempt_number")
                    or reserved.payload.get("attempt_number")
                    or 1
                ),
            )
            attempt_id = str(
                (retry or {}).get("attempt_id")
                or reserved.payload.get("attempt_id")
                or self._attempt_id(execution_id, attempt_number)
            )
            return {
                "state": "RESERVED",
                "outcome": (
                    "UNCERTAIN"
                    if reserved.payload.get("side_effecting")
                    else "PENDING"
                ),
                "event": reserved,
                "operation_id": execution_id,
                "attempt_id": attempt_id,
                "attempt_number": attempt_number,
            }
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
            if existing["state"] == "RESERVED" and not side_effecting:
                parent_id = self._execution_event_id("reserved", execution_id)
                attempt_number = max(1, int(existing.get("attempt_number") or 1)) + 1
                attempt_id = self._attempt_id(execution_id, attempt_number)
                self.append_event(
                    conversation_id,
                    "ToolExecutionRetry",
                    {
                        "execution_id": execution_id,
                        "operation_id": execution_id,
                        "attempt_id": attempt_id,
                        "attempt_number": attempt_number,
                        "tool_call_id": tool_call_id,
                        "tool_name": tool_name,
                        "arguments_digest": arguments_digest,
                    },
                    turn_id=turn_id,
                    source="tool-execution",
                    durability="SYNC",
                    replayable=True,
                    parent_event_id=parent_id,
                    event_id=self._execution_event_id(
                        f"retry-{attempt_number}", execution_id
                    ),
                )
                self.flush()
                return {
                    **existing,
                    "outcome": "PENDING",
                    "attempt_id": attempt_id,
                    "attempt_number": attempt_number,
                }
            return existing

        attempt_number = 1
        attempt_id = self._attempt_id(execution_id, attempt_number)
        record = self.append_event(
            conversation_id,
            "ToolExecutionReserved",
            {
                "execution_id": execution_id,
                "operation_id": execution_id,
                "attempt_id": attempt_id,
                "attempt_number": 1,
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
        return {
            "state": "RESERVED",
            "outcome": "PENDING",
            "event": record,
            "fresh": True,
            "operation_id": execution_id,
            "attempt_id": attempt_id,
            "attempt_number": attempt_number,
        }

    def complete_tool_execution(
        self,
        conversation_id: str,
        turn_id: str,
        *,
        execution_id: str,
        attempt_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments_digest: str,
        success: bool,
        output: str,
        error: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> SessionEventRecord:
        return self.append_event(
            conversation_id,
            "ToolExecutionCompleted",
            {
                "execution_id": execution_id,
                "operation_id": execution_id,
                "attempt_id": attempt_id,
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "arguments_digest": arguments_digest,
                "success": bool(success),
                "output": str(output or "")[:262144],
                "error": None if error is None else str(error)[:16384],
                "metadata": _jsonable(dict(metadata or {})),
            },
            turn_id=turn_id,
            source="tool-execution",
            durability="DURABLE",
            replayable=True,
            parent_event_id=self._execution_event_id("reserved", execution_id),
            event_id=self._execution_event_id("done", execution_id),
        )

    def flush(self) -> None:
        try:
            with self.db.get_connection() as conn:
                conn.commit()
                conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except Exception:
            return

    def durability_fence(
        self,
        conversation_id: str,
        *,
        turn_id: str | None,
        reason: str,
    ) -> SessionEventRecord:
        record = self.append_event(
            conversation_id,
            "DurabilityFence",
            {"reason": str(reason or "sync")[:200]},
            turn_id=turn_id,
            source="event-ledger",
            durability="SYNC",
            replayable=True,
        )
        self.flush()
        return record

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

