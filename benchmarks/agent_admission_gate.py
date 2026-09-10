#!/usr/bin/env python3
"""Compare baseline/candidate KITT agent scorecards and fail on regression."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from kitt.metrics.admission import AgentAdmissionGate, AgentScorecard


def _load(path: str) -> AgentScorecard:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return AgentScorecard.from_mapping(data)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline")
    parser.add_argument("candidate")
    parser.add_argument("--max-regression", type=float, default=0.02)
    parser.add_argument("--min-total-gain", type=float, default=0.0)
    args = parser.parse_args()
    result = AgentAdmissionGate(args.max_regression, args.min_total_gain).evaluate(
        _load(args.baseline), _load(args.candidate)
    )
    print(json.dumps({"accepted": result.accepted, "deltas": result.deltas,
                      "regressions": result.regressions}, sort_keys=True))
    return 0 if result.accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
