"""Dedicated low-overhead SQLite lineage/cache store for self-evolution."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from kitt.evolution.models import (
    CandidateMetrics,
    EvalCase,
    EvolutionCandidate,
    EvolutionRun,
    EvolutionStatus,
)
from kitt.security.private_state import workspace_state_dir


_SCHEMA = """
CREATE TABLE IF NOT EXISTS evolution_runs (
    id TEXT PRIMARY KEY,
    skill_name TEXT NOT NULL,
    status TEXT NOT NULL,
    source TEXT NOT NULL,
    baseline_sha256 TEXT NOT NULL,
    baseline_score REAL NOT NULL DEFAULT 0,
    best_candidate_id TEXT,
    created_at REAL NOT NULL,
    completed_at REAL,
    config_json TEXT NOT NULL,
    metrics_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evolution_runs_skill_created
ON evolution_runs(skill_name, created_at DESC);

CREATE TABLE IF NOT EXISTS evolution_candidates (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    parent_id TEXT,
    content TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    score REAL NOT NULL DEFAULT 0,
    metrics_json TEXT NOT NULL,
    constraints_json TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES evolution_runs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_evolution_candidates_run_score
ON evolution_candidates(run_id, score DESC, generation DESC);

CREATE TABLE IF NOT EXISTS evolution_cases (
    run_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    task TEXT NOT NULL,
    rubric TEXT NOT NULL,
    source TEXT NOT NULL,
    split TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    PRIMARY KEY(run_id, case_id),
    FOREIGN KEY(run_id) REFERENCES evolution_runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS evolution_eval_cache (
    candidate_sha256 TEXT NOT NULL,
    case_id TEXT NOT NULL,
    trial_model TEXT NOT NULL,
    judge_model TEXT NOT NULL,
    output TEXT NOT NULL,
    score_json TEXT NOT NULL,
    latency_ms REAL NOT NULL,
    output_tokens INTEGER NOT NULL,
    PRIMARY KEY(candidate_sha256, case_id, trial_model, judge_model)
);

CREATE TABLE IF NOT EXISTS evolution_promotions (
    run_id TEXT PRIMARY KEY,
    skill_name TEXT NOT NULL,
    previous_sha256 TEXT NOT NULL,
    promoted_sha256 TEXT NOT NULL,
    backup_path TEXT NOT NULL,
    promoted_at REAL NOT NULL,
    FOREIGN KEY(run_id) REFERENCES evolution_runs(id) ON DELETE CASCADE
);
"""


class EvolutionStore:
    """Separate DB keeps evolutionary writes out of KITT's hot history DB."""

    def __init__(
        self,
        root_dir: str | Path,
        *,
        state_dir: str | Path | None = None,
    ):
        state = (
            Path(state_dir).resolve()
            if state_dir is not None
            else workspace_state_dir(Path(root_dir).resolve(), "evolution")
        )
        state.mkdir(parents=True, exist_ok=True)
        self.state_dir = state
        self.path = state / "evolution.sqlite3"
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self.path,
            timeout=5.0,
            isolation_level=None,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def save_run(self, run: EvolutionRun) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO evolution_runs (
                    id, skill_name, status, source, baseline_sha256,
                    baseline_score, best_candidate_id, created_at, completed_at,
                    config_json, metrics_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status=excluded.status,
                    baseline_score=excluded.baseline_score,
                    best_candidate_id=excluded.best_candidate_id,
                    completed_at=excluded.completed_at,
                    config_json=excluded.config_json,
                    metrics_json=excluded.metrics_json
                """,
                (
                    run.id,
                    run.skill_name,
                    str(run.status),
                    run.source,
                    run.baseline_sha256,
                    float(run.baseline_score),
                    run.best_candidate_id,
                    float(run.created_at),
                    run.completed_at,
                    json.dumps(run.config, ensure_ascii=False, sort_keys=True),
                    json.dumps(run.metrics, ensure_ascii=False, sort_keys=True),
                ),
            )

    def get_run(self, run_id: str) -> EvolutionRun | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM evolution_runs WHERE id=?", (run_id,)
            ).fetchone()
        if not row:
            return None
        return EvolutionRun(
            id=row["id"],
            skill_name=row["skill_name"],
            status=EvolutionStatus(row["status"]),
            source=row["source"],
            baseline_sha256=row["baseline_sha256"],
            baseline_score=float(row["baseline_score"]),
            best_candidate_id=row["best_candidate_id"],
            created_at=float(row["created_at"]),
            completed_at=row["completed_at"],
            config=json.loads(row["config_json"] or "{}"),
            metrics=json.loads(row["metrics_json"] or "{}"),
        )

    def list_runs(self, limit: int = 30) -> list[EvolutionRun]:
        limit = max(1, min(int(limit), 200))
        with self._lock:
            ids = [
                row["id"]
                for row in self._conn.execute(
                    "SELECT id FROM evolution_runs ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            ]
        return [run for run_id in ids if (run := self.get_run(run_id)) is not None]

    def save_candidate(self, candidate: EvolutionCandidate) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO evolution_candidates (
                    id, run_id, generation, parent_id, content, content_sha256,
                    status, score, metrics_json, constraints_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status=excluded.status,
                    score=excluded.score,
                    metrics_json=excluded.metrics_json,
                    constraints_json=excluded.constraints_json
                """,
                (
                    candidate.id,
                    candidate.run_id,
                    candidate.generation,
                    candidate.parent_id,
                    candidate.content,
                    candidate.content_sha256,
                    candidate.status,
                    float(candidate.score),
                    candidate.metrics.to_json(),
                    json.dumps(candidate.constraints, ensure_ascii=False, sort_keys=True),
                ),
            )

    def get_candidate(self, candidate_id: str) -> EvolutionCandidate | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM evolution_candidates WHERE id=?", (candidate_id,)
            ).fetchone()
        if not row:
            return None
        return EvolutionCandidate(
            id=row["id"],
            run_id=row["run_id"],
            generation=int(row["generation"]),
            parent_id=row["parent_id"],
            content=row["content"],
            content_sha256=row["content_sha256"],
            status=row["status"],
            score=float(row["score"]),
            metrics=CandidateMetrics(**json.loads(row["metrics_json"] or "{}")),
            constraints=json.loads(row["constraints_json"] or "[]"),
        )

    def candidates_for_run(self, run_id: str) -> list[EvolutionCandidate]:
        with self._lock:
            ids = [
                row["id"]
                for row in self._conn.execute(
                    """
                    SELECT id FROM evolution_candidates
                    WHERE run_id=?
                    ORDER BY score DESC, generation DESC, id ASC
                    """,
                    (run_id,),
                ).fetchall()
            ]
        return [
            item
            for candidate_id in ids
            if (item := self.get_candidate(candidate_id)) is not None
        ]

    def save_cases(self, run_id: str, cases: list[EvalCase]) -> None:
        with self._lock:
            self._conn.executemany(
                """
                INSERT OR REPLACE INTO evolution_cases (
                    run_id, case_id, task, rubric, source, split, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        case.id,
                        case.task,
                        case.rubric,
                        case.source,
                        case.split,
                        json.dumps(case.metadata, ensure_ascii=False, sort_keys=True),
                    )
                    for case in cases
                ],
            )

    def cached_eval(
        self,
        candidate_sha256: str,
        case_id: str,
        trial_model: str,
        judge_model: str,
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM evolution_eval_cache
                WHERE candidate_sha256=? AND case_id=?
                  AND trial_model=? AND judge_model=?
                """,
                (candidate_sha256, case_id, trial_model, judge_model),
            ).fetchone()
        if not row:
            return None
        return {
            "output": row["output"],
            "score": json.loads(row["score_json"]),
            "latency_ms": float(row["latency_ms"]),
            "output_tokens": int(row["output_tokens"]),
        }

    def save_cached_eval(
        self,
        candidate_sha256: str,
        case_id: str,
        trial_model: str,
        judge_model: str,
        output: str,
        score: dict[str, Any],
        latency_ms: float,
        output_tokens: int,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO evolution_eval_cache (
                    candidate_sha256, case_id, trial_model, judge_model,
                    output, score_json, latency_ms, output_tokens
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate_sha256,
                    case_id,
                    trial_model,
                    judge_model,
                    output,
                    json.dumps(score, ensure_ascii=False, sort_keys=True),
                    float(latency_ms),
                    int(output_tokens),
                ),
            )

    def finalize_promotion(
        self,
        *,
        run: EvolutionRun,
        candidate: EvolutionCandidate,
        previous_sha256: str,
        promoted_sha256: str,
        backup_path: str,
        promoted_at: float,
    ) -> None:
        metrics_json = json.dumps(run.metrics, ensure_ascii=False, sort_keys=True)
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    """
                    INSERT INTO evolution_promotions (
                        run_id, skill_name, previous_sha256, promoted_sha256,
                        backup_path, promoted_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run.id,
                        run.skill_name,
                        previous_sha256,
                        promoted_sha256,
                        backup_path,
                        float(promoted_at),
                    ),
                )
                self._conn.execute(
                    """
                    UPDATE evolution_candidates
                    SET status='PROMOTED'
                    WHERE id=? AND run_id=? AND status='STAGED'
                    """,
                    (candidate.id, run.id),
                )
                if self._conn.execute("SELECT changes()").fetchone()[0] != 1:
                    raise RuntimeError("staged candidate changed during promotion")
                self._conn.execute(
                    """
                    UPDATE evolution_runs
                    SET status='PROMOTED', completed_at=?, metrics_json=?
                    WHERE id=? AND status='STAGED'
                    """,
                    (run.completed_at, metrics_json, run.id),
                )
                if self._conn.execute("SELECT changes()").fetchone()[0] != 1:
                    raise RuntimeError("evolution run changed during promotion")
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
