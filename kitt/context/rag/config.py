"""Configuration for KITT's lightweight hybrid Code-RAG layer.

The semantic layer is deliberately opt-in. Repository discovery remains the
responsibility of KITT's native Rust search/index machinery; embeddings only
rerank the bounded candidate set produced by deterministic retrieval.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


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
    return max(minimum, min(maximum, value))


@dataclass(frozen=True)
class RagConfig:
    """Runtime knobs for hybrid ranking.

    Semantic retrieval is enabled only when explicitly requested *and* an
    embedding model is configured. This prevents a normal KITT startup from
    unexpectedly contacting/pulling a model from Ollama.
    """

    semantic_enabled: bool = False
    embedding_provider: str = "ollama"
    embedding_model: str = ""
    ollama_base_url: str = "http://localhost:11434"
    embedding_timeout_seconds: float = 4.0
    embedding_batch_size: int = 24
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
            embedding_batch_size=_env_int("KITT_RAG_EMBEDDING_BATCH", 24, 2, 64),
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
