from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ExperimentArmResult:
    arm: str
    snapshot_id: str
    workspace_path: str
    primary_value: float
    guardrail_value: float
    validation_ok: bool
    evidence_refs: tuple[str, ...]
    details: dict[str, Any]


class HarnessExperimentService:
    """Controlled baseline/candidate orchestration over isolated workspaces."""

    def __init__(self, db, *, coordinator=None):
        self.db = db
        self.coordinator = coordinator

    def attach_coordinator(self, coordinator) -> "HarnessExperimentService":
        self.coordinator = coordinator
        return self

    def create(
        self,
        workspace_id: str,
        *,
        name: str,
        task: dict[str, Any],
        baseline_snapshot_id: str,
        candidate_snapshot_id: str,
        primary_metric: dict[str, Any],
        guardrail_metric: dict[str, Any],
        intervention_id: str | None = None,
    ) -> str:
        if baseline_snapshot_id == candidate_snapshot_id:
            raise ValueError("baseline and candidate snapshots must differ")
        experiment_id = f"hexp_{uuid.uuid4().hex}"
        with self.db.get_connection() as conn:
            conn.execute(
                """INSERT INTO harness_experiments(
                       id,workspace_id,intervention_id,name,task_json,
                       baseline_snapshot_id,candidate_snapshot_id,
                       primary_metric_json,guardrail_metric_json,
                       state,result_json,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,'CREATED','{}',?)""",
                (
                    experiment_id,
                    workspace_id,
                    intervention_id,
                    str(name or "experiment"),
                    json.dumps(task or {}, ensure_ascii=False, sort_keys=True),
                    baseline_snapshot_id,
                    candidate_snapshot_id,
                    json.dumps(primary_metric or {}, ensure_ascii=False, sort_keys=True),
                    json.dumps(guardrail_metric or {}, ensure_ascii=False, sort_keys=True),
                    time.time(),
                ),
            )
        return experiment_id

    @staticmethod
    def _direction(metric: dict[str, Any]) -> str:
        direction = str(metric.get("direction") or "").strip()
        if direction not in {"higher-is-better", "lower-is-better"}:
            raise ValueError("metric direction must be higher-is-better or lower-is-better")
        return direction

    @classmethod
    def _better(cls, metric: dict[str, Any], left: float, right: float) -> bool:
        direction = cls._direction(metric)
        return left > right if direction == "higher-is-better" else left < right

    @classmethod
    def _worse(cls, metric: dict[str, Any], left: float, right: float) -> bool:
        direction = cls._direction(metric)
        return left < right if direction == "higher-is-better" else left > right

    def _record_arm(
        self,
        experiment_id: str,
        result: ExperimentArmResult,
    ) -> None:
        with self.db.get_connection() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO harness_experiment_arms(
                       experiment_id,arm,snapshot_id,workspace_path,state,
                       metrics_json,evidence_json,started_at,completed_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    experiment_id,
                    result.arm,
                    result.snapshot_id,
                    result.workspace_path,
                    "COMPLETED" if result.validation_ok else "FAILED_VALIDATION",
                    json.dumps(
                        {
                            "primary_value": result.primary_value,
                            "guardrail_value": result.guardrail_value,
                            "validation_ok": result.validation_ok,
                            "details": result.details,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    json.dumps(list(result.evidence_refs), ensure_ascii=False),
                    time.time(),
                    time.time(),
                ),
            )

    def run(
        self,
        experiment_id: str,
        evaluator: Callable[[str, str, str, dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        if self.coordinator is None:
            raise RuntimeError("controlled experiment requires a workspace coordinator")
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM harness_experiments WHERE id=?",
                (experiment_id,),
            ).fetchone()
        if not row:
            raise ValueError("experiment not found")

        task = json.loads(row["task_json"] or "{}")
        primary_metric = json.loads(row["primary_metric_json"] or "{}")
        guardrail_metric = json.loads(row["guardrail_metric_json"] or "{}")
        arms = (
            ("baseline", str(row["baseline_snapshot_id"])),
            ("candidate", str(row["candidate_snapshot_id"])),
        )
        results: dict[str, ExperimentArmResult] = {}
        with self.db.get_connection() as conn:
            conn.execute(
                "UPDATE harness_experiments SET state='RUNNING' WHERE id=?",
                (experiment_id,),
            )

        try:
            for arm, snapshot_id in arms:
                owner_id = f"{experiment_id}-{arm}"
                workspace = self.coordinator.prepare_isolated_workspace(
                    owner_id,
                    namespace="experiment",
                )
                if workspace.state == "SHARED_FALLBACK":
                    raise RuntimeError(
                        "controlled experiments require a git-backed isolated worktree"
                    )
                try:
                    payload = evaluator(
                        arm,
                        workspace.path,
                        snapshot_id,
                        dict(task),
                    )
                    result = ExperimentArmResult(
                        arm=arm,
                        snapshot_id=snapshot_id,
                        workspace_path=workspace.path,
                        primary_value=float(payload["primary_value"]),
                        guardrail_value=float(payload["guardrail_value"]),
                        validation_ok=bool(payload.get("validation_ok", False)),
                        evidence_refs=tuple(payload.get("evidence_refs") or ()),
                        details=dict(payload.get("details") or {}),
                    )
                    self._record_arm(experiment_id, result)
                    results[arm] = result
                finally:
                    self.coordinator.discard_isolated_workspace(
                        owner_id,
                        delete_branch=True,
                    )

            baseline = results["baseline"]
            candidate = results["candidate"]
            guardrail_regressed = self._worse(
                guardrail_metric,
                candidate.guardrail_value,
                baseline.guardrail_value,
            )
            primary_better = self._better(
                primary_metric,
                candidate.primary_value,
                baseline.primary_value,
            )
            primary_worse = self._worse(
                primary_metric,
                candidate.primary_value,
                baseline.primary_value,
            )
            if not baseline.validation_ok or not candidate.validation_ok:
                state = "INVALID"
            elif guardrail_regressed or primary_worse:
                state = "REGRESSION"
            elif primary_better:
                state = "CANDIDATE_BETTER"
            else:
                state = "NO_CHANGE"
            result_payload = {
                "state": state,
                "baseline": {
                    "primary_value": baseline.primary_value,
                    "guardrail_value": baseline.guardrail_value,
                    "evidence_refs": list(baseline.evidence_refs),
                },
                "candidate": {
                    "primary_value": candidate.primary_value,
                    "guardrail_value": candidate.guardrail_value,
                    "evidence_refs": list(candidate.evidence_refs),
                },
            }
        except Exception as exc:
            state = "BLOCKED"
            result_payload = {"state": state, "error": str(exc)}
            with self.db.get_connection() as conn:
                conn.execute(
                    """UPDATE harness_experiments
                       SET state=?,result_json=?,completed_at=?
                       WHERE id=?""",
                    (
                        state,
                        json.dumps(result_payload, ensure_ascii=False, sort_keys=True),
                        time.time(),
                        experiment_id,
                    ),
                )
            raise

        with self.db.get_connection() as conn:
            conn.execute(
                """UPDATE harness_experiments
                   SET state=?,result_json=?,completed_at=?
                   WHERE id=?""",
                (
                    state,
                    json.dumps(result_payload, ensure_ascii=False, sort_keys=True),
                    time.time(),
                    experiment_id,
                ),
            )
        return result_payload

    def get(self, experiment_id: str) -> dict[str, Any] | None:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM harness_experiments WHERE id=?",
                (experiment_id,),
            ).fetchone()
            if not row:
                return None
            arms = conn.execute(
                """SELECT * FROM harness_experiment_arms
                   WHERE experiment_id=? ORDER BY arm""",
                (experiment_id,),
            ).fetchall()
        return {
            "id": row["id"],
            "workspace_id": row["workspace_id"],
            "name": row["name"],
            "state": row["state"],
            "task": json.loads(row["task_json"] or "{}"),
            "result": json.loads(row["result_json"] or "{}"),
            "arms": [
                {
                    "arm": arm["arm"],
                    "snapshot_id": arm["snapshot_id"],
                    "state": arm["state"],
                    "metrics": json.loads(arm["metrics_json"] or "{}"),
                    "evidence_refs": json.loads(arm["evidence_json"] or "[]"),
                }
                for arm in arms
            ],
        }
