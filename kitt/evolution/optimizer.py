"""Resource-bounded Pareto evolution using KITT's configured LLMs."""
from __future__ import annotations

import json
import re
import threading
import time
from typing import Any

from kitt.evolution.constraints import SkillConstraintValidator, reassemble_skill, split_skill
from kitt.evolution.models import CandidateMetrics, EvalCase, EvolutionCandidate, TrialScore
from kitt.evolution.store import EvolutionStore


def _estimated_tokens(text: str) -> int:
    return (len(str(text or "").encode("utf-8")) + 3) // 4


def _json_object(text: str) -> dict[str, Any]:
    value = str(text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value)
        value = re.sub(r"\s*```$", "", value)
    left, right = value.find("{"), value.rfind("}")
    if left >= 0 and right > left:
        value = value[left:right + 1]
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("expected JSON object")
    return parsed


class LLMCallBudget:
    """Hard cap so an evolution run cannot become unbounded spend."""

    def __init__(self, maximum: int = 48):
        self.maximum = max(6, min(int(maximum), 500))
        self.used = 0
        self._lock = threading.Lock()

    def consume(self, label: str) -> None:
        with self._lock:
            if self.used >= self.maximum:
                raise RuntimeError(
                    f"evolution LLM call budget exhausted ({self.used}/{self.maximum}) "
                    f"before {label}"
                )
            self.used += 1


class CandidateEvaluator:
    def __init__(
        self,
        *,
        trial_client,
        judge_client,
        store: EvolutionStore,
        budget: LLMCallBudget,
    ):
        self.trial_client = trial_client
        self.judge_client = judge_client
        self.store = store
        self.budget = budget
        self.trial_model = str(getattr(trial_client.profile, "model", "trial"))
        self.judge_model = str(getattr(judge_client.profile, "model", "judge"))

    def _trial(self, candidate: EvolutionCandidate, case: EvalCase) -> tuple[str, float]:
        cached = self.store.cached_eval(
            candidate.content_sha256,
            case.id,
            self.trial_model,
            self.judge_model,
        )
        if cached is not None:
            return str(cached["output"]), float(cached["latency_ms"])

        self.budget.consume("trial")
        started = time.perf_counter()
        output = self.trial_client.chat(
            [{"role": "user", "content": case.task}],
            system_prompt=(
                candidate.content
                + "\n\nFollow this skill faithfully. Return only the task result; "
                "do not discuss the evaluation."
            ),
        )
        return str(output or ""), (time.perf_counter() - started) * 1000.0

    def _judge(
        self,
        candidate: EvolutionCandidate,
        case: EvalCase,
        output: str,
        latency_ms: float,
    ) -> TrialScore:
        cached = self.store.cached_eval(
            candidate.content_sha256,
            case.id,
            self.trial_model,
            self.judge_model,
        )
        if cached is not None:
            score = cached["score"]
            return TrialScore(
                correctness=score["correctness"],
                procedure_following=score["procedure_following"],
                conciseness=score["conciseness"],
                output_tokens=cached["output_tokens"],
                latency_ms=cached["latency_ms"],
                feedback=score.get("feedback", ""),
            ).clamped()

        self.budget.consume("judge")
        prompt = f"""Evaluate the candidate output against the rubric.

TASK:
{case.task}

RUBRIC:
{case.rubric}

CANDIDATE OUTPUT:
{output[:12000]}

Return ONLY JSON:
{{
  "correctness": 0.0,
  "procedure_following": 0.0,
  "conciseness": 0.0,
  "feedback": "specific short feedback"
}}

Scoring:
- correctness is primary and reflects observable task success.
- procedure_following measures whether the skill's intended procedure was followed.
- conciseness rewards useful density, never omission of required work.
- Do not reward keyword repetition.
"""
        raw = self.judge_client.chat(
            [{"role": "user", "content": prompt}],
            system_prompt=(
                "You are a strict independent coding-agent evaluator. "
                "Do not optimize for agreement with the candidate. JSON only."
            ),
            response_format="json",
        )
        parsed = _json_object(raw)
        trial = TrialScore(
            correctness=float(parsed.get("correctness", 0.0)),
            procedure_following=float(parsed.get("procedure_following", 0.0)),
            conciseness=float(parsed.get("conciseness", 0.0)),
            output_tokens=_estimated_tokens(output),
            latency_ms=latency_ms,
            feedback=str(parsed.get("feedback", "")),
        ).clamped()

        self.store.save_cached_eval(
            candidate.content_sha256,
            case.id,
            self.trial_model,
            self.judge_model,
            output,
            {
                "correctness": trial.correctness,
                "procedure_following": trial.procedure_following,
                "conciseness": trial.conciseness,
                "feedback": trial.feedback,
            },
            trial.latency_ms,
            trial.output_tokens,
        )
        return trial

    def evaluate(
        self,
        candidate: EvolutionCandidate,
        cases: list[EvalCase],
    ) -> CandidateMetrics:
        trials: list[TrialScore] = []
        failures = 0
        feedback: list[str] = []

        for case in cases:
            try:
                output, latency = self._trial(candidate, case)
                score = self._judge(candidate, case, output, latency)
                trials.append(score)
                if score.feedback and len(feedback) < 12:
                    feedback.append(f"[{case.id}] {score.feedback}")
            except Exception as exc:
                failures += 1
                if len(feedback) < 12:
                    feedback.append(f"[{case.id}] evaluator failure: {exc}")

        count = len(trials)
        if count == 0:
            return CandidateMetrics(
                prompt_tokens=_estimated_tokens(candidate.content),
                failures=failures,
                feedback=feedback,
            )

        correctness = sum(t.correctness for t in trials) / count
        procedure = sum(t.procedure_following for t in trials) / count
        concise = sum(t.conciseness for t in trials) / count
        output_tokens = sum(t.output_tokens for t in trials) / count
        latency = sum(t.latency_ms for t in trials) / count
        prompt_tokens = _estimated_tokens(candidate.content)

        base = 0.62 * correctness + 0.23 * procedure + 0.15 * concise
        failure_penalty = min(0.35, failures * 0.08)
        prompt_penalty = min(0.05, prompt_tokens / 20_000.0)
        composite = max(0.0, base - failure_penalty - prompt_penalty)

        return CandidateMetrics(
            correctness=correctness,
            procedure_following=procedure,
            conciseness=concise,
            avg_output_tokens=output_tokens,
            avg_latency_ms=latency,
            prompt_tokens=prompt_tokens,
            composite=composite,
            cases=count,
            failures=failures,
            feedback=feedback,
        )


def pareto_better(candidate: CandidateMetrics, baseline: CandidateMetrics) -> bool:
    """Promotion gate: measurable improvement with no material quality regression."""
    if candidate.failures > baseline.failures:
        return False
    if candidate.correctness + 0.01 < baseline.correctness:
        return False
    if candidate.procedure_following + 0.02 < baseline.procedure_following:
        return False
    if candidate.composite < baseline.composite + 0.015:
        return False

    quality_better = (
        candidate.correctness > baseline.correctness + 0.01
        or candidate.procedure_following > baseline.procedure_following + 0.02
        or candidate.conciseness > baseline.conciseness + 0.03
    )
    efficiency_better = (
        candidate.prompt_tokens < baseline.prompt_tokens
        or candidate.avg_output_tokens < baseline.avg_output_tokens * 0.95
        or candidate.avg_latency_ms < baseline.avg_latency_ms * 0.90
    )
    return quality_better or efficiency_better


class ParetoSkillOptimizer:
    def __init__(
        self,
        *,
        mutation_client,
        evaluator: CandidateEvaluator,
        validator: SkillConstraintValidator,
        store: EvolutionStore,
        budget: LLMCallBudget,
    ):
        self.mutation_client = mutation_client
        self.evaluator = evaluator
        self.validator = validator
        self.store = store
        self.budget = budget

    def _mutate(
        self,
        baseline_content: str,
        parent: EvolutionCandidate,
        feedback: list[str],
        generation: int,
        variant_index: int,
    ) -> EvolutionCandidate:
        parts = split_skill(baseline_content)
        parent_body = split_skill(parent.content).body
        self.budget.consume("mutation")

        prompt = f"""Improve this KITT agent skill body.

Variant {variant_index + 1}, generation {generation}.

IMMUTABLE FRONTMATTER:
{parts.frontmatter}

CURRENT BODY:
{parent_body}

EVALUATION FEEDBACK:
{chr(10).join(feedback[-10:]) if feedback else "No feedback yet."}

Constraints:
- Preserve exact purpose and scope.
- Improve procedure, failure recovery, tool choice, and token efficiency.
- Prefer precise operational guidance over prose.
- Do not add capabilities, permissions, secrets, URLs, or executable code.
- Do not change name/description/frontmatter.
- Avoid model/provider-specific hacks and keyword stuffing.
- Keep the body compact.

Return ONLY JSON:
{{"body":"complete evolved markdown body"}}
"""
        raw = self.mutation_client.chat(
            [{"role": "user", "content": prompt}],
            system_prompt=(
                "You evolve coding-agent procedural skills. Make conservative, "
                "measurable improvements. JSON only."
            ),
            response_format="json",
        )
        parsed = _json_object(raw)
        body = str(parsed.get("body") or "").strip()
        if not body:
            raise ValueError("mutation returned empty body")

        return EvolutionCandidate.create(
            parent.run_id,
            generation,
            reassemble_skill(parts, body),
            parent_id=parent.id,
        )

    def optimize(
        self,
        *,
        baseline: EvolutionCandidate,
        baseline_content: str,
        train_cases: list[EvalCase],
        val_cases: list[EvalCase],
        generations: int,
        population: int,
    ) -> EvolutionCandidate:
        generations = max(1, min(int(generations), 8))
        population = max(2, min(int(population), 8))

        train_metrics = self.evaluator.evaluate(baseline, train_cases)
        baseline.metrics = self.evaluator.evaluate(baseline, val_cases)
        baseline.score = baseline.metrics.composite
        baseline.constraints = self.validator.validate(
            baseline_content, baseline.content
        )
        baseline.status = "BASELINE"
        self.store.save_candidate(baseline)

        parent = baseline
        parent_feedback = list(train_metrics.feedback)
        best = baseline

        for generation in range(1, generations + 1):
            generation_candidates: list[EvolutionCandidate] = []
            for variant_index in range(population):
                candidate = self._mutate(
                    baseline_content,
                    parent,
                    parent_feedback,
                    generation,
                    variant_index,
                )
                candidate.constraints = self.validator.validate(
                    baseline_content, candidate.content
                )
                if not all(item["passed"] for item in candidate.constraints):
                    candidate.status = "REJECTED_CONSTRAINT"
                    self.store.save_candidate(candidate)
                    continue

                candidate.metrics = self.evaluator.evaluate(candidate, val_cases)
                candidate.score = candidate.metrics.composite
                candidate.status = "VALIDATED"
                self.store.save_candidate(candidate)
                generation_candidates.append(candidate)

            if not generation_candidates:
                continue

            generation_candidates.sort(
                key=lambda item: (
                    item.metrics.correctness,
                    item.metrics.composite,
                    -item.metrics.prompt_tokens,
                    -item.metrics.avg_output_tokens,
                ),
                reverse=True,
            )
            parent = generation_candidates[0]
            parent_feedback = list(parent.metrics.feedback)

            if (
                parent.metrics.correctness > best.metrics.correctness
                or (
                    abs(parent.metrics.correctness - best.metrics.correctness) <= 0.005
                    and parent.score > best.score
                )
            ):
                best = parent

        return best
