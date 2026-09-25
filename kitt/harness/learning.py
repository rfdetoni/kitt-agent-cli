from __future__ import annotations

import json
from typing import Any


class LearningCaptureService:
    """Evidence-backed repeated-pattern discovery with a smallest-owner hint."""

    def __init__(self, db):
        self.db = db

    @staticmethod
    def _owner_hint(evidence: dict[str, dict[str, int]]) -> str:
        validation = evidence.get("change-validation", {})
        understanding = evidence.get("task-understanding", {})
        execution = evidence.get("controlled-execution", {})
        if validation.get("MISSING", 0) or validation.get("UNOBSERVED", 0):
            return "quality-gate"
        if understanding.get("MISSING", 0) or understanding.get("UNOBSERVED", 0):
            return "skill"
        if execution.get("EXERCISED", 0) >= 2:
            return "hook-or-plugin"
        return "memory"

    def candidates(
        self,
        workspace_id: str,
        *,
        min_occurrences: int = 2,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        minimum = max(2, int(min_occurrences))
        with self.db.get_connection() as conn:
            episodes = conn.execute(
                """SELECT e.id,e.objective,e.metadata_json,e.state,e.started_at
                   FROM task_episodes e
                   JOIN conversations c ON c.id=e.conversation_id
                   WHERE c.workspace_id=? AND e.state<>'OPEN'
                   ORDER BY e.started_at DESC LIMIT 500""",
                (workspace_id,),
            ).fetchall()
            evidence_rows = conn.execute(
                """SELECT r.episode_id,r.dimension,r.state
                   FROM evidence_records r
                   JOIN task_episodes e ON e.id=r.episode_id
                   JOIN conversations c ON c.id=e.conversation_id
                   WHERE c.workspace_id=?""",
                (workspace_id,),
            ).fetchall()

        by_episode: dict[str, dict[str, dict[str, int]]] = {}
        for row in evidence_rows:
            dimension = by_episode.setdefault(str(row["episode_id"]), {}).setdefault(
                str(row["dimension"]),
                {},
            )
            state = str(row["state"])
            dimension[state] = int(dimension.get(state, 0)) + 1

        grouped: dict[str, dict[str, Any]] = {}
        for row in episodes:
            meta = json.loads(row["metadata_json"] or "{}")
            normalized = str(
                meta.get("normalized_objective") or row["objective"]
            ).strip().casefold()
            bucket = grouped.setdefault(
                normalized,
                {
                    "normalized_objective": normalized,
                    "latest_objective": row["objective"],
                    "episode_ids": [],
                    "count": 0,
                    "states": {},
                    "evidence": {},
                },
            )
            bucket["count"] += 1
            bucket["episode_ids"].append(row["id"])
            bucket["states"][row["state"]] = (
                int(bucket["states"].get(row["state"], 0)) + 1
            )
            for dimension, states in by_episode.get(str(row["id"]), {}).items():
                target = bucket["evidence"].setdefault(dimension, {})
                for state, count in states.items():
                    target[state] = int(target.get(state, 0)) + int(count)

        result = []
        for bucket in grouped.values():
            if bucket["count"] < minimum:
                continue
            bucket["recommended_owner"] = self._owner_hint(bucket["evidence"])
            bucket["requires_intervention"] = True
            result.append(bucket)
        result.sort(key=lambda item: (-int(item["count"]), item["normalized_objective"]))
        return result[: max(1, min(int(limit), 100))]
