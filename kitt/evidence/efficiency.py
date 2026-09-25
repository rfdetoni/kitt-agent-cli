from __future__ import annotations

from typing import Any


class EpisodeEfficiencyService:
    """Aggregate task-level efficiency without treating missing telemetry as zero."""

    def __init__(self, db):
        self.db = db

    def summarize(self, episode_id: str) -> dict[str, Any]:
        with self.db.get_connection() as conn:
            episode = conn.execute(
                "SELECT * FROM task_episodes WHERE id=?",
                (episode_id,),
            ).fetchone()
            if not episode:
                raise ValueError("episode not found")
            turns = conn.execute(
                """SELECT turn_id FROM task_episode_turns
                   WHERE episode_id=? ORDER BY ordinal ASC""",
                (episode_id,),
            ).fetchall()
            turn_ids = [str(row["turn_id"]) for row in turns]
            if not turn_ids:
                telemetry = []
                events = []
            else:
                placeholders = ",".join("?" for _ in turn_ids)
                telemetry = conn.execute(
                    f"""SELECT * FROM telemetry_events
                        WHERE turn_id IN ({placeholders})""",
                    turn_ids,
                ).fetchall()
                events = conn.execute(
                    f"""SELECT event_type,payload_json FROM session_events
                        WHERE turn_id IN ({placeholders})""",
                    turn_ids,
                ).fetchall()

        has_telemetry = bool(telemetry)
        input_tokens = (
            sum(int(row["input_tokens"] or 0) for row in telemetry)
            if has_telemetry
            else None
        )
        output_tokens = (
            sum(int(row["output_tokens"] or 0) for row in telemetry)
            if has_telemetry
            else None
        )
        active_duration_ms = (
            sum(float(row["duration_ms"] or 0.0) for row in telemetry)
            if has_telemetry
            else None
        )
        counts: dict[str, int] = {}
        for row in events:
            event_type = str(row["event_type"])
            counts[event_type] = int(counts.get(event_type, 0)) + 1

        completed_at = episode["completed_at"]
        wall_duration_ms = (
            max(
                0.0,
                (float(completed_at) - float(episode["started_at"])) * 1000.0,
            )
            if completed_at is not None
            else None
        )
        return {
            "episode_id": episode_id,
            "turn_count": len(turn_ids),
            "wall_duration_ms": wall_duration_ms,
            "active_duration_ms": active_duration_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "model_requests": counts.get("ModelRequestPrepared", 0),
            "tool_calls": counts.get("ToolStarted", 0),
            "tool_failures": counts.get("ToolCompleted", 0)
            - sum(
                1
                for row in events
                if row["event_type"] == "ToolCompleted"
                and '"success":true' in str(row["payload_json"]).replace(" ", "").lower()
            ),
            "approval_requests": counts.get("ApprovalRequired", 0),
            "telemetry_observed": has_telemetry,
        }
