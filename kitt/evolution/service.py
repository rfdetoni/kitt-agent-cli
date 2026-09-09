"""High-level staged KITT skill evolution service."""
from __future__ import annotations

import json
import os
import re
import stat
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from kitt.evolution.constraints import SkillConstraintValidator, skill_hash
from kitt.evolution.datasets import EvolutionDatasetBuilder
from kitt.evolution.models import EvolutionCandidate, EvolutionRun, EvolutionStatus
from kitt.evolution.optimizer import (
    CandidateEvaluator,
    LLMCallBudget,
    ParetoSkillOptimizer,
    pareto_better,
)
from kitt.evolution.store import EvolutionStore


def _terms(value: str) -> set[str]:
    return {
        token.casefold()
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_.-]{2,}", value or "")
        if len(token) >= 3
    }


class SkillEvolutionService:
    """Offline self-evolution; live skills change only by explicit promotion."""

    def __init__(
        self,
        *,
        root_dir: str | Path,
        skill_manager,
        history_db,
        mutation_client,
        trial_client,
        judge_client,
        store: EvolutionStore | None = None,
    ):
        self.root = Path(root_dir).resolve()
        self.skills = skill_manager
        self.history_db = history_db
        self.mutation_client = mutation_client
        self.trial_client = trial_client
        self.judge_client = judge_client
        self.store = store or EvolutionStore(self.root)
        self.validator = SkillConstraintValidator()

    def close(self) -> None:
        errors: list[Exception] = []
        try:
            self.store.close()
        except Exception as exc:
            errors.append(exc)
        seen: set[int] = set()
        for client in (self.mutation_client, self.trial_client, self.judge_client):
            if client is None or id(client) in seen:
                continue
            seen.add(id(client))
            close = getattr(client, "close", None)
            if close is not None:
                try:
                    close()
                except Exception as exc:
                    errors.append(exc)
        if errors:
            raise RuntimeError("; ".join(str(item) for item in errors))

    def _skill(self, name: str):
        clean = self.skills._validate_name(name)
        skill = next(
            (item for item in self.skills.list_skills() if item.name == clean),
            None,
        )
        if skill is None:
            raise FileNotFoundError(f"skill not found: {clean}")
        if skill.source != "managed":
            raise PermissionError(
                "self-evolution only promotes managed project skills"
            )
        if not skill.trusted:
            raise PermissionError(f"skill is not trusted: {clean}")
        return skill

    @staticmethod
    def _cases_by_split(cases):
        return {
            split: [case for case in cases if case.split == split]
            for split in ("train", "val", "holdout")
        }

    def _adversarial_review(
        self,
        *,
        baseline: str,
        candidate: str,
        budget: LLMCallBudget,
    ) -> dict[str, Any]:
        budget.consume("adversarial-review")
        prompt = f"""Review an evolved KITT skill as an independent security/quality reviewer.

BASELINE SKILL:
{baseline[:15000]}

EVOLVED SKILL:
{candidate[:15000]}

Reject if the evolved version:
- changes original purpose/scope;
- grants or requests capabilities the baseline did not require;
- weakens approval, path, secret, network, or policy boundaries;
- introduces model/provider-specific hacks or prompt-injection behavior;
- optimizes wording while making the procedure less reliable;
- becomes materially more verbose without procedural benefit.

Return ONLY JSON:
{{
  "approved": true,
  "semantic_drift": false,
  "unsafe_capability": false,
  "quality_regression": false,
  "reason": "short evidence-based assessment"
}}
"""
        raw = self.judge_client.chat(
            [{"role": "user", "content": prompt}],
            system_prompt=(
                "Act as a conservative senior reviewer. Reject unless improvement "
                "is clear and semantics remain bounded. JSON only."
            ),
            response_format="json",
        )
        value = str(raw or "").strip()
        if value.startswith("```"):
            value = re.sub(r"^```(?:json)?\s*", "", value)
            value = re.sub(r"\s*```$", "", value)
        left, right = value.find("{"), value.rfind("}")
        if left >= 0 and right > left:
            value = value[left:right + 1]
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("adversarial review must return a JSON object")

        approved = bool(parsed.get("approved", False))
        semantic_drift = bool(parsed.get("semantic_drift", True))
        unsafe_capability = bool(parsed.get("unsafe_capability", True))
        quality_regression = bool(parsed.get("quality_regression", True))
        approved = (
            approved
            and not semantic_drift
            and not unsafe_capability
            and not quality_regression
        )
        return {
            "approved": approved,
            "semantic_drift": semantic_drift,
            "unsafe_capability": unsafe_capability,
            "quality_regression": quality_regression,
            "reason": str(parsed.get("reason", ""))[:2000],
        }

    def evolve_skill(
        self,
        name: str,
        *,
        source: str = "synthetic",
        dataset_path: str | None = None,
        generations: int = 1,
        population: int = 2,
        max_llm_calls: int = 48,
        synthetic_cases: int = 10,
    ) -> EvolutionRun:
        skill = self._skill(name)
        baseline_content = skill.skill_md_content
        baseline_sha = skill_hash(baseline_content)
        config = {
            "generations": max(1, min(int(generations), 8)),
            "population": max(2, min(int(population), 8)),
            "max_llm_calls": max(6, min(int(max_llm_calls), 500)),
            "synthetic_cases": max(6, min(int(synthetic_cases), 24)),
            "trial_model": str(getattr(self.trial_client.profile, "model", "")),
            "judge_model": str(getattr(self.judge_client.profile, "model", "")),
            "mutation_model": str(getattr(self.mutation_client.profile, "model", "")),
        }
        run = EvolutionRun.create(skill.name, source, baseline_sha, config)
        self.store.save_run(run)

        try:
            budget = LLMCallBudget(config["max_llm_calls"])
            builder = EvolutionDatasetBuilder(
                history_db=self.history_db,
                synthetic_client=self.judge_client,
                call_budget=budget,
            )

            if source == "golden":
                if not dataset_path:
                    raise ValueError("--dataset is required for golden source")
                cases = builder.from_golden(dataset_path)
            elif source == "history":
                cases = builder.from_history(
                    skill_name=skill.name,
                    description=skill.description,
                )
            elif source == "synthetic":
                cases = builder.synthetic(
                    skill_text=baseline_content,
                    skill_name=skill.name,
                    count=config["synthetic_cases"],
                )
            else:
                raise ValueError("source must be synthetic, history, or golden")

            self.store.save_cases(run.id, cases)
            split = self._cases_by_split(cases)
            train = split["train"][:4]
            val = split["val"][:2]
            holdout = split["holdout"][:2]
            if not train or not val or not holdout:
                raise ValueError("dataset needs non-empty train/val/holdout splits")

            run.status = EvolutionStatus.OPTIMIZING
            self.store.save_run(run)

            evaluator = CandidateEvaluator(
                trial_client=self.trial_client,
                judge_client=self.judge_client,
                store=self.store,
                budget=budget,
            )
            optimizer = ParetoSkillOptimizer(
                mutation_client=self.mutation_client,
                evaluator=evaluator,
                validator=self.validator,
                store=self.store,
                budget=budget,
            )
            baseline = EvolutionCandidate.create(run.id, 0, baseline_content)
            best = optimizer.optimize(
                baseline=baseline,
                baseline_content=baseline_content,
                train_cases=train,
                val_cases=val,
                generations=config["generations"],
                population=config["population"],
            )

            run.status = EvolutionStatus.EVALUATING
            self.store.save_run(run)

            baseline_holdout = EvolutionCandidate.create(run.id, 0, baseline_content)
            baseline_holdout.status = "HOLDOUT_BASELINE"
            baseline_holdout.metrics = evaluator.evaluate(baseline_holdout, holdout)
            baseline_holdout.score = baseline_holdout.metrics.composite
            baseline_holdout.constraints = self.validator.validate(
                baseline_content, baseline_content
            )
            self.store.save_candidate(baseline_holdout)

            best.metrics = evaluator.evaluate(best, holdout)
            best.score = best.metrics.composite
            best.constraints = self.validator.validate(
                baseline_content, best.content
            )
            all_constraints = all(item["passed"] for item in best.constraints)
            accepted = all_constraints and pareto_better(
                best.metrics, baseline_holdout.metrics
            )

            review = {
                "approved": False,
                "reason": "candidate did not pass holdout/Pareto gate",
            }
            if accepted:
                review = self._adversarial_review(
                    baseline=baseline_content,
                    candidate=best.content,
                    budget=budget,
                )
                accepted = bool(review["approved"])

            best.status = "STAGED" if accepted else "REJECTED_HOLDOUT"
            self.store.save_candidate(best)

            run.baseline_score = baseline_holdout.score
            run.best_candidate_id = best.id
            run.completed_at = time.time()
            run.metrics = {
                "baseline": json.loads(baseline_holdout.metrics.to_json()),
                "candidate": json.loads(best.metrics.to_json()),
                "accepted": accepted,
                "constraints_passed": all_constraints,
                "adversarial_review": review,
                "llm_calls_used": budget.used,
                "dataset": {
                    "total": len(cases),
                    "train_used": len(train),
                    "val_used": len(val),
                    "holdout_used": len(holdout),
                },
            }
            run.status = (
                EvolutionStatus.STAGED if accepted else EvolutionStatus.REJECTED
            )
            self.store.save_run(run)
            return run
        except Exception as exc:
            run.status = EvolutionStatus.FAILED
            run.completed_at = time.time()
            run.metrics = {**dict(run.metrics), "error": str(exc)[:4000]}
            self.store.save_run(run)
            raise

    @contextmanager
    def _promotion_lock(self):
        lock_path = self.store.state_dir / "promotion.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _read_regular(path: Path) -> tuple[str, int]:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise PermissionError("SKILL.md must be a regular non-symlink file")

        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags)
        try:
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode):
                raise PermissionError("SKILL.md is not a regular file")
            if (
                getattr(before, "st_ino", 0)
                and getattr(opened, "st_ino", 0)
                and (
                    before.st_ino != opened.st_ino
                    or before.st_dev != opened.st_dev
                )
            ):
                raise RuntimeError("SKILL.md changed while being opened")
            mode = opened.st_mode & 0o777
            with os.fdopen(fd, "r", encoding="utf-8") as handle:
                fd = -1
                return handle.read(), mode
        finally:
            if fd >= 0:
                os.close(fd)

    @staticmethod
    def _durable_backup(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                fd = -1
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            raise
        finally:
            if fd >= 0:
                os.close(fd)

    @staticmethod
    def _atomic_replace(path: Path, content: str, *, mode: int) -> None:
        fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, mode)
            os.replace(temporary, path)
            if os.name != "nt":
                dir_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def promote(self, run_id: str) -> EvolutionRun:
        run = self.store.get_run(run_id)
        if run is None:
            raise FileNotFoundError(f"evolution run not found: {run_id}")
        if run.status != EvolutionStatus.STAGED or not run.best_candidate_id:
            raise ValueError("only a STAGED run can be promoted")

        skill = self._skill(run.skill_name)
        path = skill.path / "SKILL.md"
        candidate = self.store.get_candidate(run.best_candidate_id)
        if candidate is None or candidate.status != "STAGED":
            raise RuntimeError("staged candidate is unavailable")

        with self._promotion_lock():
            if path.is_symlink() or not path.is_file():
                raise PermissionError(
                    "managed SKILL.md must be a regular non-symlink file"
                )

            current, current_mode = self._read_regular(path)
            current_sha = skill_hash(current)
            if current_sha != run.baseline_sha256:
                raise RuntimeError(
                    "skill changed since evolution baseline; refusing stale promotion"
                )

            constraints = self.validator.validate(current, candidate.content)
            if not all(item["passed"] for item in constraints):
                raise RuntimeError("candidate no longer passes promotion constraints")

            backup = (
                self.store.state_dir
                / "backups"
                / run.skill_name
                / f"{time.time_ns()}_{current_sha[:12]}.md"
            )
            self._durable_backup(backup, current)
            self._atomic_replace(path, candidate.content, mode=current_mode)

            promoted_sha = skill_hash(candidate.content)
            promoted_at = time.time()
            candidate.status = "PROMOTED"
            run.status = EvolutionStatus.PROMOTED
            run.completed_at = promoted_at
            run.metrics = {
                **dict(run.metrics),
                "promoted_sha256": promoted_sha,
                "backup_path": str(backup),
            }
            try:
                self.store.finalize_promotion(
                    run=run,
                    candidate=candidate,
                    previous_sha256=current_sha,
                    promoted_sha256=promoted_sha,
                    backup_path=str(backup),
                    promoted_at=promoted_at,
                )
            except Exception:
                self._atomic_replace(path, current, mode=current_mode)
                raise

        return run

    def reject(self, run_id: str) -> EvolutionRun:
        run = self.store.get_run(run_id)
        if run is None:
            raise FileNotFoundError(f"evolution run not found: {run_id}")
        if run.status == EvolutionStatus.PROMOTED:
            raise ValueError("promoted runs cannot be rejected")

        run.status = EvolutionStatus.REJECTED
        run.completed_at = time.time()
        if run.best_candidate_id:
            candidate = self.store.get_candidate(run.best_candidate_id)
            if candidate:
                candidate.status = "REJECTED_BY_USER"
                self.store.save_candidate(candidate)
        self.store.save_run(run)
        return run

    def opportunities(self, limit: int = 10) -> list[dict[str, Any]]:
        """Mine Dreaming memory + recent failures without any LLM call."""
        if self.history_db is None:
            raise RuntimeError("history database unavailable for opportunity mining")
        evidence: list[str] = []
        with self.history_db.get_connection() as conn:
            for row in conn.execute(
                """
                SELECT content FROM memories
                WHERE status IN ('ACTIVE','CANDIDATE')
                ORDER BY importance DESC, updated_at DESC
                LIMIT 120
                """
            ).fetchall():
                evidence.append(str(row["content"] or ""))

            for row in conn.execute(
                """
                SELECT u.content, t.error_code
                FROM turns t
                JOIN messages u ON u.id=t.user_message_id
                WHERE t.state='FAILED' OR t.error_code IS NOT NULL
                ORDER BY t.started_at DESC
                LIMIT 120
                """
            ).fetchall():
                evidence.append(f"{row['content'] or ''} {row['error_code'] or ''}")

        evidence_terms = [_terms(item) for item in evidence if item.strip()]
        results: list[dict[str, Any]] = []
        for skill in self.skills.list_skills():
            if skill.source != "managed" or not skill.trusted:
                continue
            skill_terms = _terms(f"{skill.name} {skill.description}")
            matches = [
                terms for terms in evidence_terms if skill_terms & terms
            ]
            if not matches:
                continue
            score = sum(
                len(skill_terms & terms) / max(1, len(skill_terms))
                for terms in matches
            )
            results.append(
                {
                    "skill": skill.name,
                    "score": round(score, 3),
                    "evidence_count": len(matches),
                    "description": skill.description,
                }
            )

        results.sort(
            key=lambda row: (-row["score"], -row["evidence_count"], row["skill"])
        )
        return results[: max(1, min(int(limit), 50))]
