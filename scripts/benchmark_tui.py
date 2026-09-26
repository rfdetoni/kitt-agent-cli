#!/usr/bin/env python3
from __future__ import annotations

import json
import statistics
import time
from types import SimpleNamespace

from kitt.ui.render.core import _transcript_text
from kitt.ui.state import TranscriptBlock


def measure_ms(fn, iterations: int) -> list[float]:
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000)
    return samples


def percentile(samples: list[float], ratio: float) -> float:
    ordered = sorted(samples)
    index = min(len(ordered) - 1, int(len(ordered) * ratio))
    return ordered[index]


blocks = [
    TranscriptBlock(
        id=f"block-{index}",
        kind="assistant" if index % 3 else "tool",
        text=("result " + str(index) + " ") * 20,
        status="done",
    )
    for index in range(500)
]
state = SimpleNamespace(
    transcript=blocks,
    active_turn_id=None,
    is_thinking=False,
    is_executing_tool=False,
    unseen_output=False,
)
ui = SimpleNamespace(state=state)

cold_started = time.perf_counter()
_transcript_text(ui)
cold_ms = (time.perf_counter() - cold_started) * 1000
warm = measure_ms(lambda: _transcript_text(ui), 500)

print(json.dumps({
    "service": "kitt-agent-cli",
    "benchmark_version": 1,
    "scenario": "transcript_500_blocks",
    "cold_ms": round(cold_ms, 3),
    "warm_mean_ms": round(statistics.fmean(warm), 3),
    "warm_p50_ms": round(percentile(warm, 0.50), 3),
    "warm_p95_ms": round(percentile(warm, 0.95), 3),
    "warm_p99_ms": round(percentile(warm, 0.99), 3),
    "cache_entries": len(getattr(ui, "_transcript_render_cache", {})),
}, indent=2))
