from __future__ import annotations

import json
import os
import time
import uuid

from .models import InvariantResult


class RuntimeInvariantService:
    """Package-owned runtime consistency checks.

    Default mode is OBSERVE: failures are durable diagnostics, never a hidden
    behavior change. STRICT may be enabled explicitly for fail-fast validation.
    """

    def __init__(self, db, *, mode: str | None = None):
        self.db = db
        configured = str(mode or os.getenv("KITT_RUNTIME_INVARIANTS", "observe")).strip().upper()
        self.mode = configured if configured in {"OFF", "OBSERVE", "STRICT"} else "OBSERVE"

    def _store(self, conversation_id: str, turn_id: str, result: InvariantResult) -> None:
        if self.mode == "OFF":
            return
        with self.db.get_connection() as conn:
            conn.execute(
                """INSERT INTO runtime_invariant_results(
                       id,conversation_id,turn_id,invariant_name,ok,critical,detail,created_at
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    f"inv_{uuid.uuid4().hex}",
                    conversation_id,
                    turn_id,
                    result.name,
                    int(result.ok),
                    int(result.critical),
                    result.detail,
                    time.time(),
                ),
            )

    def check_terminal(self, conversation_id: str, turn_id: str) -> list[InvariantResult]:
        if self.mode == "OFF":
            return []
        results: list[InvariantResult] = []
        with self.db.get_connection() as conn:
            pending = int(
                conn.execute(
                    """SELECT COUNT(*) FROM pending_actions
                       WHERE conversation_id=? AND turn_id=? AND state='pending'""",
                    (conversation_id, turn_id),
                ).fetchone()[0]
            )
            results.append(
                InvariantResult(
                    "terminal_has_no_pending_actions",
                    pending == 0,
                    "" if pending == 0 else f"{pending} pending action(s) remain",
                    True,
                )
            )

            orphan = int(
                conn.execute(
                    """SELECT COUNT(*) FROM pending_actions p
                       LEFT JOIN approval_requests a ON a.approval_id=p.approval_request_id
                       WHERE p.conversation_id=? AND p.turn_id=? AND p.state='pending'
                         AND a.approval_id IS NULL""",
                    (conversation_id, turn_id),
                ).fetchone()[0]
            )
            results.append(
                InvariantResult(
                    "pending_action_has_approval_owner",
                    orphan == 0,
                    "" if orphan == 0 else f"{orphan} pending action(s) have no approval owner",
                    True,
                )
            )

            model_requests = int(
                conn.execute(
                    """SELECT COUNT(*) FROM session_events
                       WHERE conversation_id=? AND turn_id=?
                         AND event_type='ModelRequestPrepared'""",
                    (conversation_id, turn_id),
                ).fetchone()[0]
            )
            model_selected = int(
                conn.execute(
                    """SELECT COUNT(*) FROM session_events
                       WHERE conversation_id=? AND turn_id=? AND event_type='ModelSelected'""",
                    (conversation_id, turn_id),
                ).fetchone()[0]
            )
            results.append(
                InvariantResult(
                    "model_request_reconstructible",
                    model_selected == 0 or model_requests > 0,
                    (
                        ""
                        if model_selected == 0 or model_requests > 0
                        else "model execution was selected but no durable model request exists"
                    ),
                    True,
                )
            )

            rows = conn.execute(
                """SELECT event_type,payload_json,sequence FROM session_events
                   WHERE conversation_id=? AND turn_id=?
                     AND event_type IN ('ToolStarted','ToolCompleted')
                   ORDER BY sequence ASC""",
                (conversation_id, turn_id),
            ).fetchall()
        started: set[str] = set()
        unmatched = 0
        for row in rows:
            payload = json.loads(row["payload_json"] or "{}")
            call_id = str(payload.get("call_id") or "")
            if row["event_type"] == "ToolStarted" and call_id:
                started.add(call_id)
            elif row["event_type"] == "ToolCompleted" and call_id and call_id not in started:
                unmatched += 1
        results.append(
            InvariantResult(
                "tool_result_has_prior_call",
                unmatched == 0,
                "" if unmatched == 0 else f"{unmatched} tool result(s) lack a prior call event",
                False,
            )
        )

        for result in results:
            self._store(conversation_id, turn_id, result)
        failures = [result for result in results if not result.ok and result.critical]
        if failures and self.mode == "STRICT":
            joined = "; ".join(f"{item.name}: {item.detail}" for item in failures)
            raise RuntimeError(f"KITT runtime invariant failure: {joined}")
        return results
