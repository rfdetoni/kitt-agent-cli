"""Configuration for KITT's lightweight hybrid Code-RAG layer."""
from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import asdict, dataclass


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    if not math.isfinite(value):
        value = default
    return max(minimum, min(maximum, value))


@dataclass(frozen=True)
class RagConfig:
    """Bounded runtime knobs for native-first hybrid retrieval."""

    semantic_enabled: bool = False
    embedding_provider: str = "ollama"
    embedding_model: str = ""
    ollama_base_url: str = "http://localhost:11434"
    embedding_timeout_seconds: float = 4.0
    embedding_total_timeout_seconds: float = 8.0
    embedding_batch_size: int = 24
    embedding_cache_size: int = 256
    max_embedding_chars: int = 6000
    semantic_candidate_limit: int = 24
    native_candidate_limit: int = 32
    native_context_lines: int = 2
    native_token_budget: int = 1400
    rrf_k: int = 60
    lexical_weight: float = 1.00
    symbol_weight: float = 1.35
    semantic_weight: float = 1.20
    graph_weight: float = 0.75
    working_set_weight: float = 0.90
    git_weight: float = 0.95
    test_weight: float = 0.85

    def fingerprint(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    @classmethod
    def from_env(cls) -> "RagConfig":
        model = os.getenv("KITT_RAG_EMBEDDING_MODEL", "").strip()
        semantic_requested = _env_bool("KITT_RAG_SEMANTIC_ENABLED", False)
        return cls(
            semantic_enabled=semantic_requested and bool(model),
            embedding_provider=os.getenv("KITT_RAG_EMBEDDING_PROVIDER", "ollama").strip().lower(),
            embedding_model=model,
            ollama_base_url=os.getenv("KITT_RAG_OLLAMA_URL", "http://localhost:11434").strip(),
            embedding_timeout_seconds=_env_float("KITT_RAG_EMBEDDING_TIMEOUT", 4.0, 0.5, 30.0),
            embedding_total_timeout_seconds=_env_float(
                "KITT_RAG_EMBEDDING_TOTAL_TIMEOUT", 8.0, 0.5, 60.0
            ),
            embedding_batch_size=_env_int("KITT_RAG_EMBEDDING_BATCH", 24, 2, 64),
            embedding_cache_size=_env_int("KITT_RAG_EMBEDDING_CACHE", 256, 0, 4096),
            max_embedding_chars=_env_int("KITT_RAG_MAX_EMBEDDING_CHARS", 6000, 512, 32000),
            semantic_candidate_limit=_env_int("KITT_RAG_SEMANTIC_CANDIDATES", 24, 4, 96),
            native_candidate_limit=_env_int("KITT_RAG_NATIVE_CANDIDATES", 32, 4, 128),
            native_context_lines=_env_int("KITT_RAG_NATIVE_CONTEXT_LINES", 2, 0, 8),
            native_token_budget=_env_int("KITT_RAG_NATIVE_TOKEN_BUDGET", 1400, 256, 8000),
            rrf_k=_env_int("KITT_RAG_RRF_K", 60, 1, 500),
            lexical_weight=_env_float("KITT_RAG_WEIGHT_LEXICAL", 1.0, 0.0, 4.0),
            symbol_weight=_env_float("KITT_RAG_WEIGHT_SYMBOL", 1.35, 0.0, 4.0),
            semantic_weight=_env_float("KITT_RAG_WEIGHT_SEMANTIC", 1.20, 0.0, 4.0),
            graph_weight=_env_float("KITT_RAG_WEIGHT_GRAPH", 0.75, 0.0, 4.0),
            working_set_weight=_env_float("KITT_RAG_WEIGHT_WORKING_SET", 0.90, 0.0, 4.0),
            git_weight=_env_float("KITT_RAG_WEIGHT_GIT", 0.95, 0.0, 4.0),
            test_weight=_env_float("KITT_RAG_WEIGHT_TEST", 0.85, 0.0, 4.0),
        )
