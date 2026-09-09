"""Evaluation dataset sources for skill evolution."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from kitt.evolution.models import EvalCase


def _json_payload(text: str) -> Any:
    value = str(text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value)
        value = re.sub(r"\s*```$", "", value)
    left, right = value.find("["), value.rfind("]")
    if left >= 0 and right > left:
        value = value[left:right + 1]
    return json.loads(value)


def _terms(value: str) -> set[str]:
    return {
        token.casefold()
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_.-]{2,}", value or "")
        if len(token) >= 3
    }


def _split(cases: list[EvalCase]) -> list[EvalCase]:
    result: list[EvalCase] = []
    for case in cases:
        bucket = int(hashlib.sha256(case.id.encode()).hexdigest()[:8], 16) % 10
        split = "holdout" if bucket >= 8 else ("val" if bucket >= 6 else "train")
        result.append(
            EvalCase(
                id=case.id,
                task=case.task,
                rubric=case.rubric,
                source=case.source,
                split=split,
                metadata=case.metadata,
            )
        )

    if len(result) >= 3:
        missing = [
            name
            for name in ("train", "val", "holdout")
            if name not in {item.split for item in result}
        ]
        for index, name in enumerate(missing):
            item = result[index % len(result)]
            result[index % len(result)] = EvalCase(
                id=item.id,
                task=item.task,
                rubric=item.rubric,
                source=item.source,
                split=name,
                metadata=item.metadata,
            )
    return result


class EvolutionDatasetBuilder:
    def __init__(self, history_db=None, synthetic_client=None, call_budget=None):
        self.history_db = history_db
        self.synthetic_client = synthetic_client
        self.call_budget = call_budget

    def from_golden(self, path: str | Path, limit: int = 32) -> list[EvalCase]:
        cases: list[EvalCase] = []
        with Path(path).open("r", encoding="utf-8") as handle:
            for line in handle:
                if len(cases) >= limit:
                    break
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                task = str(row.get("task") or row.get("input") or "").strip()
                rubric = str(
                    row.get("rubric")
                    or row.get("expected_behavior")
                    or row.get("expected")
                    or ""
                ).strip()
                if task and rubric:
                    cases.append(
                        EvalCase.create(
                            task,
                            rubric,
                            "golden",
                            metadata={
                                key: value
                                for key, value in row.items()
                                if key not in {
                                    "task",
                                    "input",
                                    "rubric",
                                    "expected_behavior",
                                    "expected",
                                }
                            },
                        )
                    )
        if len(cases) < 3:
            raise ValueError("golden dataset requires at least 3 valid JSONL cases")
        return _split(cases)

    def from_history(
        self,
        *,
        skill_name: str,
        description: str,
        limit: int = 18,
    ) -> list[EvalCase]:
        if self.history_db is None:
            raise RuntimeError("history database unavailable")

        needles = _terms(f"{skill_name} {description}")
        with self.history_db.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT t.id AS turn_id, t.state, t.error_code,
                       u.content AS user_content,
                       a.content AS assistant_content
                FROM turns t
                JOIN messages u ON u.id=t.user_message_id
                LEFT JOIN messages a ON a.id=t.assistant_message_id
                WHERE u.content <> ''
                ORDER BY t.started_at DESC
                LIMIT 400
                """
            ).fetchall()

        ranked: list[tuple[float, Any]] = []
        for row in rows:
            task = str(row["user_content"] or "")
            overlap = len(needles & _terms(task)) / max(1, len(needles))
            failure_bonus = 0.25 if str(row["state"]).upper() == "FAILED" else 0.0
            explicit_bonus = 0.5 if skill_name.casefold() in task.casefold() else 0.0
            score = overlap + failure_bonus + explicit_bonus
            if score > 0:
                ranked.append((score, row))
        ranked.sort(key=lambda item: item[0], reverse=True)

        cases: list[EvalCase] = []
        for _, row in ranked[:limit]:
            task = str(row["user_content"] or "").strip()
            response = str(row["assistant_content"] or "").strip()
            if row["error_code"]:
                rubric = (
                    "Solve the task correctly and avoid the previous failure. "
                    f"Previous error code: {row['error_code']}."
                )
            elif response:
                rubric = (
                    "Solve the task correctly and preserve useful behavior from this "
                    "historical reference without copying it mechanically:\n"
                    + response[:2500]
                )
            else:
                rubric = "Solve the task correctly, safely, and concisely."

            cases.append(
                EvalCase.create(
                    task,
                    rubric,
                    "history",
                    metadata={"turn_id": row["turn_id"]},
                )
            )
        if len(cases) < 3:
            raise ValueError(
                "not enough relevant historical cases; use synthetic or golden source"
            )
        return _split(cases)

    def synthetic(
        self,
        *,
        skill_text: str,
        skill_name: str,
        count: int = 10,
    ) -> list[EvalCase]:
        if self.synthetic_client is None:
            raise RuntimeError("synthetic dataset requires an LLM client")

        count = max(6, min(int(count), 24))
        if self.call_budget is not None:
            self.call_budget.consume("synthetic-dataset")

        prompt = f"""Generate {count} diverse evaluation cases for the agent skill below.

Return ONLY a JSON array. Each object:
{{"task":"realistic user task","rubric":"specific observable criteria for success"}}

Rules:
- Include easy, edge, failure-recovery, and ambiguous cases.
- Rubrics must describe behavior, not exact wording.
- Do not include answers to the tasks.
- Test the skill's actual purpose.

SKILL NAME: {skill_name}

SKILL:
{skill_text[:15000]}
"""
        raw = self.synthetic_client.chat(
            [{"role": "user", "content": prompt}],
            system_prompt=(
                "You build adversarial evaluation datasets for coding-agent skills. "
                "Return strict JSON only."
            ),
        )
        payload = _json_payload(raw)
        if not isinstance(payload, list):
            raise ValueError("synthetic evaluator did not return a JSON array")

        cases: list[EvalCase] = []
        for row in payload[:count]:
            if not isinstance(row, dict):
                continue
            task = str(row.get("task") or "").strip()
            rubric = str(row.get("rubric") or "").strip()
            if task and rubric:
                cases.append(EvalCase.create(task, rubric, "synthetic"))
        if len(cases) < 6:
            raise ValueError("synthetic dataset produced fewer than 6 valid cases")
        return _split(cases)
