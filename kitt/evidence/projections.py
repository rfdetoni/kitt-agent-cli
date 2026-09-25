from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Callable

from .models import SessionEventRecord


StateFactory = Callable[[], Any]
Reducer = Callable[[Any, SessionEventRecord], Any]
View = Callable[[Any], Any]


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ProjectionDefinition:
    key: str
    version: int
    initial: StateFactory
    apply: Reducer
    view: View = lambda state: state


class SessionProjectionRegistry:
    """Pure event folds with sparse durable checkpoints and a hot in-memory cache."""

    def __init__(self, db=None, *, checkpoint_interval: int = 8):
        self.db = db
        self.checkpoint_interval = max(1, int(checkpoint_interval))
        self._definitions: dict[str, ProjectionDefinition] = {}
        self._memory: dict[tuple[str, str, int], tuple[int, Any]] = {}

    def attach(self, db) -> "SessionProjectionRegistry":
        self.db = db
        return self

    def register(self, definition: ProjectionDefinition) -> None:
        if not definition.key or definition.version < 1:
            raise ValueError("projection key and positive version are required")
        current = self._definitions.get(definition.key)
        if current is not None and current.version != definition.version:
            raise ValueError(f"projection already registered: {definition.key}")
        self._definitions[definition.key] = definition

    def keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._definitions))

    @staticmethod
    def _row_to_event(row) -> SessionEventRecord:
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

    def _seed(self, conversation_id: str, definition: ProjectionDefinition) -> tuple[int, Any]:
        if self.db is None:
            return 0, definition.initial()
        with self.db.get_connection() as conn:
            row = conn.execute(
                """SELECT sequence,state_json FROM session_projection_cache
                   WHERE conversation_id=? AND projection_key=? AND projection_version=?""",
                (conversation_id, definition.key, definition.version),
            ).fetchone()
            sequence = int(row["sequence"]) if row else 0
            state = json.loads(row["state_json"]) if row else definition.initial()
            rows = conn.execute(
                """SELECT * FROM session_events
                   WHERE conversation_id=? AND sequence>?
                   ORDER BY sequence ASC""",
                (conversation_id, sequence),
            ).fetchall()
        for event_row in rows:
            event = self._row_to_event(event_row)
            state = definition.apply(state, event)
            sequence = event.sequence
        return sequence, state

    def observe(self, event: SessionEventRecord, *, force_checkpoint: bool = False) -> None:
        for definition in self._definitions.values():
            cache_key = (event.conversation_id, definition.key, definition.version)
            sequence, state = self._memory.get(cache_key, (0, None))
            seeded_through_current = False
            if state is None or sequence + 1 != event.sequence:
                sequence, state = self._seed(event.conversation_id, definition)
                seeded_through_current = sequence >= event.sequence
            if not seeded_through_current:
                state = definition.apply(state, event)
                sequence = event.sequence
            self._memory[cache_key] = (sequence, state)
            if self.db is None:
                continue
            if not force_checkpoint and sequence % self.checkpoint_interval:
                continue
            state_json = _canonical(state)
            with self.db.get_connection() as conn:
                conn.execute(
                    """INSERT INTO session_projection_cache(
                           conversation_id,projection_key,projection_version,sequence,
                           state_json,state_hash,updated_at
                       ) VALUES(?,?,?,?,?,?,?)
                       ON CONFLICT(conversation_id,projection_key,projection_version)
                       DO UPDATE SET sequence=excluded.sequence,state_json=excluded.state_json,
                           state_hash=excluded.state_hash,updated_at=excluded.updated_at""",
                    (
                        event.conversation_id,
                        definition.key,
                        definition.version,
                        sequence,
                        state_json,
                        _digest(state),
                        time.time(),
                    ),
                )

    def snapshot(self, conversation_id: str, key: str) -> Any:
        definition = self._definitions[key]
        cache_key = (conversation_id, definition.key, definition.version)
        sequence, state = self._memory.get(cache_key, (0, None))
        if state is None:
            sequence, state = self._seed(conversation_id, definition)
            self._memory[cache_key] = (sequence, state)
        return definition.view(state)


def _turn_state(state: dict[str, Any], event: SessionEventRecord) -> dict[str, Any]:
    mapping = {
        "TurnStarted": "RUNNING",
        "FilterCompleted": "FILTERING",
        "ContextResolved": "RETRIEVING",
        "ContextBuildCompleted": "RETRIEVING",
        "BudgetApplied": "READY",
        "ModelSelected": "ROUTING",
        "ToolStarted": "EXECUTING",
        "ToolCompleted": "EXECUTING",
        "ApprovalRequired": "WAITING_APPROVAL",
        "ValidationCompleted": "VALIDATING",
        "TurnCompleted": "COMPLETED",
        "TurnFailed": "FAILED",
        "TurnBlocked": "BLOCKED",
        "TurnCancelled": "CANCELLED",
    }
    next_state = mapping.get(event.event_type)
    if next_state is None:
        return state
    return {
        "turn_id": event.turn_id or state.get("turn_id", ""),
        "state": next_state,
        "sequence": event.sequence,
        "updated_at": event.created_at,
    }


def _tool_activity(state: dict[str, Any], event: SessionEventRecord) -> dict[str, Any]:
    if event.event_type not in {"ToolStarted", "ToolCompleted"}:
        return state
    result = dict(state)
    result.setdefault("started", 0)
    result.setdefault("completed", 0)
    result.setdefault("failed", 0)
    if event.event_type == "ToolStarted":
        result["started"] += 1
    else:
        result["completed"] += 1
        if event.payload.get("success") is False:
            result["failed"] += 1
    result["last_tool"] = str(event.payload.get("tool_name") or "")
    result["sequence"] = event.sequence
    return result


def _episode_state(state: dict[str, Any], event: SessionEventRecord) -> dict[str, Any]:
    if event.event_type == "EpisodeOpened":
        return {
            "episode_id": event.episode_id or event.payload.get("episode_id", ""),
            "state": "OPEN",
            "objective": event.payload.get("objective", ""),
            "sequence": event.sequence,
        }
    if event.event_type == "EpisodeSettled":
        result = dict(state)
        result["state"] = event.payload.get("state", "UNKNOWN")
        result["sequence"] = event.sequence
        return result
    return state


def _model_requests(state: dict[str, Any], event: SessionEventRecord) -> dict[str, Any]:
    if event.event_type != "ModelRequestPrepared":
        return state
    return {
        "count": int(state.get("count", 0)) + 1,
        "last_hash": event.payload_hash,
        "last_model": event.payload.get("model", ""),
        "last_route": event.payload.get("route", ""),
        "sequence": event.sequence,
    }


def build_default_projection_registry(db=None) -> SessionProjectionRegistry:
    registry = SessionProjectionRegistry(db)
    registry.register(
        ProjectionDefinition(
            "turn_state",
            1,
            lambda: {"turn_id": "", "state": "IDLE", "sequence": 0},
            _turn_state,
        )
    )
    registry.register(
        ProjectionDefinition(
            "tool_activity",
            1,
            lambda: {"started": 0, "completed": 0, "failed": 0, "last_tool": "", "sequence": 0},
            _tool_activity,
        )
    )
    registry.register(
        ProjectionDefinition(
            "episode",
            1,
            lambda: {"episode_id": "", "state": "NONE", "objective": "", "sequence": 0},
            _episode_state,
        )
    )
    registry.register(
        ProjectionDefinition(
            "model_requests",
            1,
            lambda: {"count": 0, "last_hash": "", "last_model": "", "last_route": "", "sequence": 0},
            _model_requests,
        )
    )
    return registry
