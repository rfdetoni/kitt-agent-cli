"""CLI entrypoint for low-overhead offline KITT self-evolution."""
from __future__ import annotations

import json
from pathlib import Path

from kitt.core.runtime_config import RuntimeConfig
from kitt.evolution.egress import EvolutionEgressClient
from kitt.evolution.service import SkillEvolutionService
from kitt.evolution.store import EvolutionStore
from kitt.history.database import HistoryDatabase
from kitt.llm.client import LLMClient
from kitt.router.router import TaskRouter
from kitt.skills.skill_manager import SkillManager


def _service(
    root_dir: str,
    *,
    with_llms: bool = False,
    with_history: bool = False,
):
    root = Path(root_dir).resolve()
    history_db = HistoryDatabase(root) if with_history else None
    skills = SkillManager(root)

    mutation_client = None
    trial_client = None
    judge_client = None

    if with_llms:
        router = TaskRouter(root_dir=root)
        _, mutation_profile = router.resolve_profile_for_task("code-generation")
        _, trial_profile = router.resolve_profile_for_task("code-generation")
        _, judge_profile = router.resolve_profile_for_task("context-gather")
        privacy_mode = getattr(
            RuntimeConfig.from_env(), "privacy_mode", "hybrid_redacted"
        )

        mutation_client = EvolutionEgressClient(
            LLMClient(mutation_profile),
            root_dir=root,
            privacy_mode=privacy_mode,
        )
        trial_client = EvolutionEgressClient(
            LLMClient(trial_profile),
            root_dir=root,
            privacy_mode=privacy_mode,
        )
        judge_client = EvolutionEgressClient(
            LLMClient(judge_profile),
            root_dir=root,
            privacy_mode=privacy_mode,
        )

    service = SkillEvolutionService(
        root_dir=root,
        skill_manager=skills,
        history_db=history_db,
        mutation_client=mutation_client,
        trial_client=trial_client,
        judge_client=judge_client,
        store=EvolutionStore(root),
    )
    return service, history_db


def _summary(run) -> dict:
    return {
        "id": run.id,
        "skill": run.skill_name,
        "status": str(run.status),
        "source": run.source,
        "baseline_score": round(run.baseline_score, 4),
        "best_candidate_id": run.best_candidate_id,
        "created_at": run.created_at,
        "completed_at": run.completed_at,
        "metrics": run.metrics,
    }


def handle_evolve_command(args) -> int:
    action = args.evolve_action or "runs"
    with_llms = action == "skill"
    with_history = (
        action == "opportunities"
        or (action == "skill" and getattr(args, "source", "") == "history")
    )
    service, history_db = _service(
        args.root,
        with_llms=with_llms,
        with_history=with_history,
    )
    primary_error = None
    try:

        if action == "skill":
            run = service.evolve_skill(
                args.skill_name,
                source=args.source,
                dataset_path=args.dataset,
                generations=args.generations,
                population=args.population,
                max_llm_calls=args.max_calls,
                synthetic_cases=args.cases,
            )
            print(json.dumps(_summary(run), ensure_ascii=False, indent=2))
            if str(run.status) == "STAGED":
                print(
                    f"\nCandidate staged. Promote explicitly with:\n"
                    f"  kitt evolve promote {run.id}"
                )
            return 0 if str(run.status) in {"STAGED", "REJECTED"} else 1

        if action == "runs":
            rows = service.store.list_runs(
                limit=int(getattr(args, "limit", 30) or 30)
            )
            if not rows:
                print("No evolution runs.")
                return 0
            for run in rows:
                print(
                    f"{run.id}  {str(run.status):<10}  {run.skill_name:<24} "
                    f"baseline={run.baseline_score:.3f}  "
                    f"best={run.best_candidate_id or '-'}"
                )
            return 0

        if action == "show":
            run = service.store.get_run(args.run_id)
            if not run:
                print(f"Evolution run not found: {args.run_id}")
                return 2
            payload = _summary(run)
            payload["candidates"] = [
                {
                    "id": item.id,
                    "generation": item.generation,
                    "status": item.status,
                    "score": round(item.score, 4),
                    "sha256": item.content_sha256,
                    "metrics": json.loads(item.metrics.to_json()),
                    "constraints": item.constraints,
                }
                for item in service.store.candidates_for_run(run.id)
            ]
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0

        if action == "promote":
            run = service.promote(args.run_id)
            print(json.dumps(_summary(run), ensure_ascii=False, indent=2))
            return 0

        if action == "reject":
            run = service.reject(args.run_id)
            print(json.dumps(_summary(run), ensure_ascii=False, indent=2))
            return 0

        if action == "opportunities":
            rows = service.opportunities(
                limit=int(getattr(args, "limit", 10) or 10)
            )
            if not rows:
                print(
                    "No evolution opportunities detected from "
                    "Dreaming memory/recent failures."
                )
                return 0
            print(json.dumps(rows, ensure_ascii=False, indent=2))
            return 0

        raise ValueError(f"unsupported evolve action: {action}")
    except Exception as exc:
        primary_error = exc
        raise
    finally:
        close_errors = []
        try:
            service.close()
        except Exception as exc:
            close_errors.append(exc)
        if history_db is not None:
            try:
                history_db.close()
            except Exception as exc:
                close_errors.append(exc)
        if close_errors and primary_error is None:
            raise RuntimeError(
                "evolution shutdown failed: "
                + "; ".join(str(item) for item in close_errors)
            )
