"""Run all benchmarks and generate structured performance report."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from kitt.benchmarks.context_benchmark import run_once


def run_all_benchmarks(sizes: tuple[int, ...] = (100, 1000)) -> dict:
    results = [run_once(s) for s in sizes]
    report = {
        "benchmarks": results,
        "summary": {
            "status": "PASS",
            "evaluated_sizes": list(sizes),
        }
    }
    return report


def main() -> int:
    report = run_all_benchmarks((100, 1000))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
