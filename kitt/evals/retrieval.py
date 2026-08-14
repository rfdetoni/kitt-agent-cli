from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from kitt.context.query_plan import QueryPlanner
from kitt.context.retrieval import HybridRetrievalPipeline
from kitt.index.repository import RepositoryIndex


@dataclass(frozen=True)
class RetrievalCase:
    name: str
    prompt: str
    expected_path: str


CASES = (
    RetrievalCase("python exact symbol", "fix `target_symbol`", "app.py"),
    RetrievalCase("tail content", "rare tail marker", "tail.py"),
    RetrievalCase("traceback path", 'Traceback:\n  File "crash.py", line 2, in run\nValueError: boom', "crash.py"),
    RetrievalCase("paired tests", "add tests for `calculate_total`", "test_calc.py"),
    RetrievalCase("git focus", "analise arquivos alterados no git", "changed.py"),
)


def run_eval() -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "app.py").write_text("def target_symbol():\n    return 1\n", encoding="utf-8")
        (root / "tail.py").write_text(("x = 1\n" * 900) + "rare_tail_marker = True\n", encoding="utf-8")
        (root / "crash.py").write_text("def run():\n    raise ValueError('boom')\n", encoding="utf-8")
        (root / "calc.py").write_text("def calculate_total():\n    return 42\n", encoding="utf-8")
        (root / "test_calc.py").write_text("def test_calculate_total():\n    assert calculate_total() == 42\n", encoding="utf-8")
        subprocess.run(["git", "init"], cwd=tmp, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        subprocess.run(["git", "add", "."], cwd=tmp, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        subprocess.run(
            ["git", "-c", "user.email=kitt@example.invalid", "-c", "user.name=KITT Eval", "commit", "-m", "base"],
            cwd=tmp,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        (root / "changed.py").write_text("def changed_context():\n    return 7\n", encoding="utf-8")
        index = RepositoryIndex(root, in_memory=True)
        stats = index.build_or_update()
        pipeline = HybridRetrievalPipeline(index)
        hit_at_1 = 0
        hit_at_5 = 0
        reciprocal_ranks = []
        details = []
        for case in CASES:
            plan = QueryPlanner.plan(case.prompt, token_budget=1200)
            selected = pipeline.retrieve(case.prompt, explicit_files=set(plan.exact_paths), max_tokens=1200)
            paths = [candidate.path for candidate in selected if candidate.path][:5]
            rank = next((idx for idx, path in enumerate(paths, 1) if path == case.expected_path), 0)
            hit_at_1 += int(rank == 1)
            hit_at_5 += int(rank > 0)
            reciprocal_ranks.append(1 / rank if rank else 0)
            details.append({
                "case": case.name,
                "prompt": case.prompt,
                "expected": case.expected_path,
                "paths": paths,
                "rank": rank,
                "ok": bool(rank),
            })
        index.close()
        total = len(CASES)
        return {
            "cases": total,
            "recall_at_1": round(hit_at_1 / total, 3),
            "recall_at_5": round(hit_at_5 / total, 3),
            "mrr": round(sum(reciprocal_ranks) / total, 3),
            "index": {
                "state": stats["state"],
                "generation": stats["generation"],
                "schema_version": stats["schema_version"],
            },
            "details": details,
        }


def main() -> int:
    print(json.dumps(run_eval(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
