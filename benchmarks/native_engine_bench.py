#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import statistics
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kitt.native.bridge import NativeCodeEngine


def bench(fn, rounds: int = 10) -> dict:
    samples = []
    value = None
    for _ in range(rounds):
        started = time.perf_counter()
        value = fn()
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    p95 = samples[min(len(samples)-1, int(len(samples) * .95))]
    return {"mean_ms": statistics.mean(samples), "p95_ms": p95, "min_ms": min(samples), "last": value}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("repo", type=Path)
    parser.add_argument("--query", default="Service")
    parser.add_argument("--rounds", type=int, default=8)
    args = parser.parse_args()
    engine = NativeCodeEngine(str(args.repo.resolve()))
    search = bench(lambda: engine.search(args.query, max_results=30, token_budget=1200), args.rounds)
    symbols = bench(lambda: engine.find_symbols(args.query, limit=30), args.rounds)
    print(json.dumps({
        "backend": engine.status.__dict__,
        "search": {k: v for k, v in search.items() if k != "last"},
        "search_hits": len(search["last"].get("hits", [])),
        "symbols": {k: v for k, v in symbols.items() if k != "last"},
        "symbol_hits": len(symbols["last"]),
    }, indent=2))


if __name__ == "__main__":
    main()
