"""Deterministic review transaction microbenchmark (not an LLM task benchmark)."""
from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
import tracemalloc
from pathlib import Path
from types import SimpleNamespace

from kitt.runtime.workspace_review import WorkspaceReview


class InMemoryState:
    def __init__(self):
        self.rows = {}
    def get(self, key):
        import copy
        return copy.deepcopy(self.rows.get(key))
    def set(self, key, value, ttl_seconds=None):
        import copy
        self.rows[key] = copy.deepcopy(value)
    def compare_and_swap_status(self, key, expected, value):
        if self.rows[key]["status"] != expected:
            return False
        self.set(key, value)
        return True


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    at = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * p)))
    return ordered[at]


def run(iterations: int, file_bytes: int) -> dict:
    samples = []
    with tempfile.TemporaryDirectory(prefix="kitt-review-benchmark-") as root:
        runtime = SimpleNamespace(root=Path(root), state=InMemoryState())
        review = WorkspaceReview(runtime)
        before = "x" * file_bytes
        after = "y" * file_bytes
        file = Path(root) / "bench.txt"
        file.write_text(before, encoding="utf-8")
        for _ in range(10):
            rid = review.preview({"files":[{"path":"bench.txt","content":after}]}, None)["review_id"]
            review.apply({"review_id":rid}, None)
            review.rewind({"review_id":rid}, None)
        tracemalloc.start()
        for _ in range(iterations):
            started = time.perf_counter_ns()
            rid = review.preview({"files":[{"path":"bench.txt","content":after}]}, None)["review_id"]
            review.apply({"review_id":rid}, None)
            review.rewind({"review_id":rid}, None)
            samples.append((time.perf_counter_ns() - started) / 1_000_000)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert file.read_text() == before
    return {
        "scenario": "single_file_preview_apply_rewind",
        "iterations": iterations, "file_bytes": file_bytes,
        "p50_ms": round(statistics.median(samples), 3),
        "p95_ms": round(percentile(samples, 0.95), 3),
        "min_ms": round(min(samples), 3),
        "max_ms": round(max(samples), 3),
        "peak_tracemalloc_bytes": peak,
        "limitations": "In-memory state, local filesystem, no model/API/SQLite overhead",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--file-bytes", type=int, default=1024)
    options = parser.parse_args()
    if not 1 <= options.iterations <= 1000 or not 1 <= options.file_bytes <= 4096:
        parser.error("iterations must be 1..1000 and file-bytes 1..4096")
    print(json.dumps(run(options.iterations, options.file_bytes), sort_keys=True))
