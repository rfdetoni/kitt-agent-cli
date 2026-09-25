from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, is_dataclass
from typing import Any


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def _canonical(value: Any) -> str:
    return json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class HarnessEvolutionService:
    """Evidence-first harness snapshots and intervention lifecycle."""

    def __init__(self, db, harness_repository):
        self.db = db
        self.repo = harness_repository

    def capture_snapshot(
        self,
        workspace_id: str,
        conversation_id: str | None = None,
        *,
        runtime_facts: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entries = self.repo.active(workspace_id, conversation_id)
        components = [
            {
                "kind": entry.entry_kind,
                "scope": entry.scope,
                "name": entry.name,
                "version": entry.version,
                "content_hash": entry.content_hash,
                "status": entry.status,
            }
            for entry in entries
        ]
        payload = {
            "components": components,
            "runtime": _jsonable(runtime_facts or {}),
        }
        snapshot_id = f"hs_{_digest(payload)[:32]}"
        now = time.time()
        with self.db.get_connection() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO harness_snapshots(
                       id,workspace_id,conversation_id,snapshot_hash,payload_json,created_at
                   ) VALUES(?,?,?,?,?,?)""",
                (
                    snapshot_id,
                    workspace_id,
                    conversation_id,
                    _digest(payload),
                    _canonical(payload),
                    now,
                ),
            )
        return {
            "id": snapshot_id,
            "snapshot_hash": _digest(payload),
            "payload": payload,
            "created_at": now,
        }

    def record_materializations(
        self,
        snapshot_id: str,
        receipts: list[dict[str, Any]],
    ) -> list[str]:
        created: list[str] = []
        now = time.time()
        with self.db.get_connection() as conn:
            for receipt in receipts:
                receipt_id = f"hmr_{uuid.uuid4().hex}"
                created.append(receipt_id)
                conn.execute(
                    """INSERT INTO harness_materialization_receipts(
                           id,snapshot_id,component_kind,component_id,requested,resolved,
                           materialized,mechanism,detail_json,created_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        receipt_id,
                        snapshot_id,
                        str(receipt.get("component_kind") or ""),
                        str(receipt.get("component_id") or ""),
                        int(bool(receipt.get("requested"))),
                        int(bool(receipt.get("resolved"))),
                        int(bool(receipt.get("materialized"))),
                        str(receipt.get("mechanism") or ""),
                        _canonical(receipt.get("detail") or {}),
                        now,
                    ),
                )
        return created

    def record_materialization(
        self,
        snapshot_id: str,
        *,
        component_kind: str,
        component_id: str,
        requested: bool,
        resolved: bool,
        materialized: bool,
        mechanism: str,
        detail: dict[str, Any] | None = None,
    ) -> str:
        return self.record_materializations(
            snapshot_id,
            [{
                "component_kind": component_kind,
                "component_id": component_id,
                "requested": requested,
                "resolved": resolved,
                "materialized": materialized,
                "mechanism": mechanism,
                "detail": detail or {},
            }],
        )[0]

    def create_intervention(
        self,
        workspace_id: str,
        *,
        source_episode_id: str | None,
        asset_type: str,
        asset_ref: str,
        owner: str,
        candidate_causes: list[dict[str, Any]],
        primary_metric: dict[str, Any],
        guardrail_metric: dict[str, Any],
        baseline: dict[str, Any],
        comparison_window: dict[str, Any],
        validation: dict[str, Any],
        stop_or_revert_condition: str,
    ) -> str:
        if len(candidate_causes) < 2:
            raise ValueError("at least two evidence-labelled candidate causes are required")
        iid = f"hint_{uuid.uuid4().hex}"
        payload = {
            "candidate_causes": candidate_causes,
            "primary_metric": primary_metric,
            "guardrail_metric": guardrail_metric,
            "baseline": baseline,
            "comparison_window": comparison_window,
            "validation": validation,
            "stop_or_revert_condition": str(stop_or_revert_condition),
        }
        with self.db.get_connection() as conn:
            conn.execute(
                """INSERT INTO harness_interventions(
                       id,workspace_id,source_episode_id,asset_type,asset_ref,owner,
                       proposal_json,state,result_json,created_at
                   ) VALUES(?,?,?,?,?,?,?,'PROPOSED','{}',?)""",
                (
                    iid,
                    workspace_id,
                    source_episode_id,
                    str(asset_type),
                    str(asset_ref),
                    str(owner),
                    _canonical(payload),
                    time.time(),
                ),
            )
        return iid

    def mark_applied(self, intervention_id: str) -> None:
        with self.db.get_connection() as conn:
            updated = conn.execute(
                """UPDATE harness_interventions
                   SET state='APPLIED',applied_at=?
                   WHERE id=? AND state='PROPOSED'""",
                (time.time(), intervention_id),
            )
        if updated.rowcount != 1:
            raise ValueError("intervention is missing or not PROPOSED")

    @staticmethod
    def _metric_improved(direction: str, before: float, after: float) -> bool:
        if direction == "lower-is-better":
            return after < before
        if direction == "higher-is-better":
            return after > before
        raise ValueError("metric direction must be higher-is-better or lower-is-better")

    @staticmethod
    def _metric_worsened(direction: str, before: float, after: float) -> bool:
        if direction == "lower-is-better":
            return after > before
        if direction == "higher-is-better":
            return after < before
        raise ValueError("metric direction must be higher-is-better or lower-is-better")

    def record_comparison(
        self,
        intervention_id: str,
        *,
        primary_value: float,
        guardrail_value: float,
        evidence_refs: list[str],
        comparable: bool,
        outcome_evidence_refs: list[str] | None = None,
    ) -> str:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM harness_interventions WHERE id=?",
                (intervention_id,),
            ).fetchone()
        if not row:
            raise ValueError("intervention not found")
        proposal = json.loads(row["proposal_json"])
        baseline = proposal["baseline"]
        primary_metric = proposal["primary_metric"]
        guardrail_metric = proposal["guardrail_metric"]
        before_primary = float(baseline["primary_value"])
        before_guardrail = float(baseline["guardrail_value"])
        after_primary = float(primary_value)
        after_guardrail = float(guardrail_value)

        guardrail_worse = self._metric_worsened(
            str(guardrail_metric["direction"]),
            before_guardrail,
            after_guardrail,
        )
        primary_better = self._metric_improved(
            str(primary_metric["direction"]),
            before_primary,
            after_primary,
        )
        primary_worse = self._metric_worsened(
            str(primary_metric["direction"]),
            before_primary,
            after_primary,
        )

        if not comparable:
            state = "UNOBSERVED"
        elif guardrail_worse or primary_worse:
            state = "REGRESSING"
        elif primary_better:
            state = "OUTCOME_SUPPORTED" if outcome_evidence_refs else "IMPROVING"
        else:
            state = "UNCHANGED"

        result = {
            "state": state,
            "comparable": bool(comparable),
            "primary_value": after_primary,
            "guardrail_value": after_guardrail,
            "evidence_refs": list(evidence_refs),
            "outcome_evidence_refs": list(outcome_evidence_refs or []),
        }
        with self.db.get_connection() as conn:
            conn.execute(
                """UPDATE harness_interventions
                   SET state=?,result_json=?,compared_at=?
                   WHERE id=?""",
                (state, _canonical(result), time.time(), intervention_id),
            )
        return state

    def learning_candidates(
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
            meta = json.loads(row["metadata_json"] or "{}")
            key = str(meta.get("normalized_objective") or row["objective"]).strip().casefold()
            bucket = groups.setdefault(
                key,
                {
                    "normalized_objective": key,
                    "count": 0,
                    "episode_ids": [],
                    "latest_objective": row["objective"],
                },
            )
            bucket["count"] += 1
            bucket["episode_ids"].append(row["id"])
        candidates = [item for item in groups.values() if item["count"] >= minimum]
        candidates.sort(key=lambda item: (-int(item["count"]), item["normalized_objective"]))
        return candidates[: max(1, min(int(limit), 100))]
