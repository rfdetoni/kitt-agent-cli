#!/usr/bin/env python3
from __future__ import annotations

import json
import time

from kitt.context.token_ledger import TokenLedger
from kitt.context_filter.prompt_budget import PromptBudget


def timed(fn):
    started = time.perf_counter()
    result = fn()
    return result, (time.perf_counter() - started) * 1000


sizes = [32 * 1024, 256 * 1024, 1024 * 1024]
rows = []

for size in sizes:
    payload = ("0123456789abcdef" * ((size // 16) + 1))[:size]
    budget = PromptBudget(window_size=131072, reserved_output=4096)
    _, truncate_ms = timed(lambda: budget._truncate_to_tokens(payload, 8192))

    messages = [
        {"role": "user", "content": payload[: size // 2]},
        {"role": "tool", "content": payload[size // 2 :]},
    ]
    ledger = TokenLedger()
    _, cold_ledger_ms = timed(lambda: ledger.total_input_tokens("system", messages))
    _, warm_ledger_ms = timed(lambda: ledger.total_input_tokens("system", messages))

    rows.append({
        "payload_bytes": size,
        "truncate_ms": round(truncate_ms, 3),
        "ledger_cold_ms": round(cold_ledger_ms, 3),
        "ledger_warm_ms": round(warm_ledger_ms, 3),
        "ledger": ledger.snapshot(),
    })

print(json.dumps({
    "service": "kitt-agent-cli",
    "benchmark_version": 1,
    "scenario": "context_budget_and_token_ledger",
    "rows": rows,
}, indent=2))
