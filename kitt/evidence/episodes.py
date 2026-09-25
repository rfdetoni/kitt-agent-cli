from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any

from .ledger import SessionLedger
from .models import EVIDENCE_STRENGTH, EvidenceRecord, EvidenceState, TaskEpisode


_TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "BLOCKED"}


def _normalize_objective(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip().casefold())
    return text[:1000]


class TaskEpisodeService:
    """Task-level evidence boundary above individual turns/sessions."""

    def __init__(self, db, ledger: SessionLedger, goal_service=None):
        self.db = db
        self.ledger = ledger
        self.goals = goal_service

    @staticmethod
    def _row(row) -> TaskEpisode:
        return TaskEpisode(
            id=row["id"],
            conversation_id=row["conversation_id"],
            goal_id=row["goal_id"],
            objective=row["objective"],
            acceptance=tuple(json.loads(row["acceptance_json"] or "[]")),
            state=row["state"],
            started_at=float(row["started_at"]),
            completed_at=row["completed_at"],
            outcome=json.loads(row["outcome_json"] or "{}"),
            metadata=json.loads(row["metadata_json"] or "{}"),
        )

    def _goal(self, conversation_id: str):
        if self.goals is None:
            return None
        try:
            return self.goals.active(conversation_id)
        except Exception:
            return None

    def begin_turn(self, conversation_id: str, turn_id: str, objective: str) -> TaskEpisode:
        goal = self._goal(conversation_id)
        goal_id = getattr(goal, "id", None)
        acceptance = tuple(getattr(goal, "success_criteria", ()) or ())
        episode = None
        with self.db.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if goal_id:
                row = conn.execute(
                    """SELECT * FROM task_episodes
                       WHERE conversation_id=? AND goal_id=? AND state='OPEN'
                       ORDER BY started_at DESC LIMIT 1""",
                    (conversation_id, goal_id),
                ).fetchone()
                if row:
                    episode = self._row(row)
            if episode is None:
                eid = f"ep_{uuid.uuid4().hex}"
                now = time.time()
                effective_objective = str(getattr(goal, "objective", "") or objective).strip()
                conn.execute(
                    """INSERT INTO task_episodes(
                           id,conversation_id,goal_id,objective,acceptance_json,state,
                           started_at,metadata_json
                       ) VALUES(?,?,?,?,?,'OPEN',?,?)""",
                    (
                        eid,
                        conversation_id,
                        goal_id,
                        effective_objective,
                        json.dumps(list(acceptance), ensure_ascii=False),
                        now,
                        json.dumps(
                            {"normalized_objective": _normalize_objective(effective_objective)},
                            ensure_ascii=False,
                        ),
                    ),
                )
                row = conn.execute("SELECT * FROM task_episodes WHERE id=?", (eid,)).fetchone()
                episode = self._row(row)
            ordinal = int(
                conn.execute(
                    "SELECT COALESCE(MAX(ordinal),0)+1 FROM task_episode_turns WHERE episode_id=?",
                    (episode.id,),
                ).fetchone()[0]
            )
            conn.execute(
                """INSERT OR IGNORE INTO task_episode_turns(episode_id,turn_id,ordinal)
                   VALUES(?,?,?)""",
                (episode.id, turn_id, ordinal),
            )
        if ordinal == 1:
            self.ledger.append(
                conversation_id,
                "EpisodeOpened",
                {
                    "episode_id": episode.id,
                    "objective": episode.objective,
                    "acceptance": list(episode.acceptance),
                    "goal_id": episode.goal_id,
                },
                turn_id=turn_id,
                episode_id=episode.id,
                force_checkpoint=True,
            )
        self.record_evidence(
            episode.id,
            "task-understanding",
            "intent-and-acceptance",
            EvidenceState.PRESENT,
            result="Task objective captured in the durable episode boundary.",
            evidence_refs=(f"turn:{turn_id}",),
        )
        return episode

    def for_turn(self, turn_id: str) -> TaskEpisode | None:
        with self.db.get_connection() as conn:
            row = conn.execute(
                """SELECT e.* FROM task_episode_turns t
                   JOIN task_episodes e ON e.id=t.episode_id
                   WHERE t.turn_id=?""",
                (turn_id,),
            ).fetchone()
        return self._row(row) if row else None

    def record_evidence(
        self,
        episode_id: str,
        dimension: str,
        check_id: str,
        state: EvidenceState | str,
        *,
        result: str = "",
        evidence_refs: tuple[str, ...] | list[str] = (),
        finding_refs: tuple[str, ...] | list[str] = (),
    ) -> EvidenceRecord:
        normalized = EvidenceState(str(state))
        now = time.time()
        with self.db.get_connection() as conn:
            current = conn.execute(
                """SELECT * FROM evidence_records
                   WHERE episode_id=? AND dimension=? AND check_id=?""",
                (episode_id, dimension, check_id),
            ).fetchone()
            if current:
                current_state = EvidenceState(current["state"])
                if EVIDENCE_STRENGTH.get(current_state, 0) > EVIDENCE_STRENGTH.get(normalized, 0):
                    normalized = current_state
                record_id = current["id"]
                created_at = float(current["created_at"])
            else:
                record_id = f"ev_{uuid.uuid4().hex}"
                created_at = now
            conn.execute(
                """INSERT INTO evidence_records(
                       id,episode_id,dimension,check_id,state,result,
                       evidence_refs_json,finding_refs_json,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(episode_id,dimension,check_id)
                   DO UPDATE SET state=excluded.state,result=excluded.result,
                       evidence_refs_json=excluded.evidence_refs_json,
                       finding_refs_json=excluded.finding_refs_json""",
                (
                    record_id,
                    episode_id,
                    dimension,
                    check_id,
                    normalized.value,
                    str(result or ""),
                    json.dumps(list(evidence_refs), ensure_ascii=False),
                    json.dumps(list(finding_refs), ensure_ascii=False),
                    created_at,
                ),
            )
        return EvidenceRecord(
            id=record_id,
            episode_id=episode_id,
            dimension=dimension,
            check_id=check_id,
            state=normalized,
            result=str(result or ""),
            evidence_refs=tuple(evidence_refs),
            finding_refs=tuple(finding_refs),
            created_at=created_at,
        )

    def record_deliverables(
        self,
        episode_id: str,
        turn_id: str,
        paths: list[str] | tuple[str, ...],
        *,
        kind: str,
        content_hashes: dict[str, str] | None = None,
    ) -> None:
        hashes = content_hashes or {}
        now = time.time()
        with self.db.get_connection() as conn:
            for path in dict.fromkeys(str(path) for path in paths if str(path)):
                conn.execute(
                    """INSERT INTO episode_deliverables(
                           id,episode_id,turn_id,kind,path,content_hash,created_at
                       ) VALUES(?,?,?,?,?,?,?)""",
                    (
                        f"del_{uuid.uuid4().hex}",
                        episode_id,
                        turn_id,
                        kind,
                        path,
                        hashes.get(path),
                        now,
                    ),
                )

    def observe(
        self,
        episode_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        event_ref: str,
    ) -> None:
        refs = (event_ref,)
        if event_type in {"ContextResolved", "ContextBuildCompleted"}:
            self.record_evidence(
                episode_id,
                "task-understanding",
                "relevant-context",
                EvidenceState.EXERCISED,
                result="Context selection was exercised for this episode.",
                evidence_refs=refs,
            )
        elif event_type == "ToolCompleted":
            self.record_evidence(
                episode_id,
                "controlled-execution",
                "supported-operation",
                EvidenceState.EXERCISED,
                result=(
                    "Tool execution succeeded."
                    if payload.get("success") is not False
                    else "Tool execution was exercised and returned a failure."
                ),
                evidence_refs=refs,
            )
        elif event_type == "ApprovalRequired":
            self.record_evidence(
                episode_id,
                "controlled-execution",
                "permission-boundary",
                EvidenceState.EXERCISED,
                result="The permission boundary was exercised.",
                evidence_refs=refs,
            )
        elif event_type == "ValidationCompleted":
            self.record_evidence(
                episode_id,
                "change-validation",
                "relevant-check",
                EvidenceState.EXERCISED,
                result=(
                    "Validation completed successfully."
                    if payload.get("success") is not False
                    else "Validation completed with failures."
                ),
                evidence_refs=refs,
            )

    def settle_for_turn(self, turn_id: str, state: str, *, outcome: dict[str, Any] | None = None) -> None:
        episode = self.for_turn(turn_id)
        if episode is None or episode.state != "OPEN":
            return
        goal = self._goal(episode.conversation_id)
        if episode.goal_id and goal is not None and getattr(goal, "id", None) == episode.goal_id:
            if str(getattr(goal, "state", "")).upper() not in _TERMINAL:
                return
        terminal = str(state or "").upper()
        mapped = {
            "COMPLETED": "SUCCEEDED",
            "FAILED": "FAILED",
            "CANCELLED": "CANCELLED",
            "BLOCKED": "BLOCKED",
        }.get(terminal, terminal if terminal in _TERMINAL else "FAILED")
        now = time.time()
        with self.db.get_connection() as conn:
            conn.execute(
                """UPDATE task_episodes
                   SET state=?,completed_at=?,outcome_json=?
                   WHERE id=? AND state='OPEN'""",
                (mapped, now, json.dumps(outcome or {}, ensure_ascii=False), episode.id),
            )
        self.ledger.append(
            episode.conversation_id,
            "EpisodeSettled",
            {"episode_id": episode.id, "state": mapped, "outcome": outcome or {}},
            turn_id=turn_id,
            episode_id=episode.id,
            force_checkpoint=True,
        )

    def repeated_objectives(
        self,
        workspace_id: str,
        *,
        min_occurrences: int = 2,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        minimum = max(2, int(min_occurrences))
        with self.db.get_connection() as conn:
            rows = conn.execute(
                """SELECT e.id,e.objective,e.metadata_json,e.state,e.started_at
                   FROM task_episodes e
                   JOIN conversations c ON c.id=e.conversation_id
                   WHERE c.workspace_id=? AND e.state<>'OPEN'
                   ORDER BY e.started_at DESC LIMIT 500""",
                (workspace_id,),
            ).fetchall()
        groups: dict[str, dict[str, Any]] = {}
        for row in rows:
            metadata = json.loads(row["metadata_json"] or "{}")
            key = str(metadata.get("normalized_objective") or _normalize_objective(row["objective"]))
            bucket = groups.setdefault(
                key,
                {"normalized_objective": key, "count": 0, "episode_ids": [], "latest_objective": row["objective"]},
            )
            bucket["count"] += 1
            bucket["episode_ids"].append(row["id"])
        result = [value for value in groups.values() if value["count"] >= minimum]
        result.sort(key=lambda item: (-int(item["count"]), item["normalized_objective"]))
        return result[: max(1, min(int(limit), 100))]
