"""Cost estimation per turn based on token usage and configurable pricing table."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

# Default prices in USD per 1M tokens
DEFAULT_PRICE_TABLE: Dict[str, Dict[str, float]] = {
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-opus-4-6": {"input": 15.0, "output": 75.0},
    "gpt-4o": {"input": 2.5, "output": 10.0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gemini-2.0-flash": {"input": 0.075, "output": 0.30},
    "default": {"input": 1.0, "output": 3.0},
}

PRICE_TABLE = DEFAULT_PRICE_TABLE

_cached_mtimes: Dict[str, float] = {}
_cached_prices: Dict[str, Dict[str, float]] = {}


def _load_pricing_file(path: Path) -> Dict[str, Dict[str, float]]:
    if not path.is_file():
        return {}
    try:
        mtime = path.stat().st_mtime
        cache_key = str(path.resolve())
        if cache_key in _cached_mtimes and _cached_mtimes[cache_key] == mtime:
            return _cached_prices.get(cache_key, {})
        data = json.loads(path.read_text(encoding="utf-8"))
        res = {}
        if isinstance(data, dict):
            for model, rates in data.items():
                if isinstance(rates, dict):
                    inp = float(rates.get("input", 1.0))
                    out = float(rates.get("output", 3.0))
                    if inp >= 0.0 and out >= 0.0:
                        res[str(model)] = {"input": inp, "output": out}
        _cached_mtimes[cache_key] = mtime
        _cached_prices[cache_key] = res
        return res
    except Exception:
        return {}


def get_merged_pricing(workspace_root: Optional[str] = None) -> Dict[str, Dict[str, float]]:
    merged = dict(DEFAULT_PRICE_TABLE)
    user_file = Path.home() / ".kitt" / "pricing.json"
    merged.update(_load_pricing_file(user_file))
    if workspace_root:
        ws_file = Path(workspace_root) / ".kitt" / "pricing.json"
        merged.update(_load_pricing_file(ws_file))
    return merged


@dataclass(frozen=True)
class TurnCost:
    model: str
    input_tokens: int
    output_tokens: int
    estimated_usd: float


def estimate_cost(model: str, input_tokens: int, output_tokens: int, workspace_root: Optional[str] = None) -> TurnCost:
    pricing = get_merged_pricing(workspace_root)
    prices = pricing.get(model, pricing.get("default", DEFAULT_PRICE_TABLE["default"]))
    cost = (input_tokens * prices["input"] + output_tokens * prices["output"]) / 1_000_000
    return TurnCost(model=model, input_tokens=input_tokens, output_tokens=output_tokens, estimated_usd=cost)
