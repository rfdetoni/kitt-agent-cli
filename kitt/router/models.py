"""Routing domain contracts and immutable dataclasses."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Tuple, Optional

COMPLEXITY_LEVELS = {"LOW", "MEDIUM", "HIGH"}
RISK_LEVELS = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
PRIVACY_MODES = {"offline", "local_only", "hybrid_redacted", "cloud_allowed"}
FEATURE_SOURCES = {"deterministic", "semantic", "merged"}


@dataclass(frozen=True)
class TaskFeatures:
    intent: str
    secondary_intents: Tuple[str, ...]
    complexity: str  # LOW|MEDIUM|HIGH
    risk: str  # LOW|MEDIUM|HIGH|CRITICAL
    requires_repository: bool
    requires_tools: bool
    requires_validation: bool
    estimated_files: int
    cross_module: bool
    prompt_tokens: int
    expected_context_tokens: int
    ambiguity: float
    confidence: float
    languages: Tuple[str, ...]
    paths: Tuple[str, ...]
    symbols: Tuple[str, ...]
    actions: Tuple[str, ...]
    source: str  # deterministic|semantic|merged

    def __post_init__(self):
        if self.complexity not in COMPLEXITY_LEVELS:
            raise ValueError(f"Invalid complexity {self.complexity!r}")
        if self.risk not in RISK_LEVELS:
            raise ValueError(f"Invalid risk {self.risk!r}")
        if not (0.0 <= self.ambiguity <= 1.0):
            raise ValueError(f"Ambiguity must be 0..1, got {self.ambiguity}")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"Confidence must be 0..1, got {self.confidence}")
        if self.source not in FEATURE_SOURCES:
            raise ValueError(f"Invalid feature source {self.source!r}")


@dataclass(frozen=True)
class ModelCapabilities:
    profile_name: str
    tier: str  # "small" | "large"
    input_context_limit: int
    max_output_tokens: int
    supports_json: bool
    supports_native_tools: bool
    tool_call_reliability: float
    code_edit_score: float
    reasoning_score: float
    languages: Tuple[str, ...]
    is_local: bool
    privacy_class: str
    tokens_per_second: Optional[float] = None
    cold_start_ms: Optional[int] = None
    memory_cost_mb: Optional[int] = None
    max_parallel_requests: int = 4
    cost_per_million_tokens: Optional[float] = None
    health: str = "healthy"  # healthy|degraded|unhealthy
    current_load: float = 0.0

    def __post_init__(self):
        if self.input_context_limit <= 0 or self.max_output_tokens <= 0:
            raise ValueError("Context limits must be positive integers")
        if math.isnan(self.tool_call_reliability) or math.isnan(self.code_edit_score) or math.isnan(self.reasoning_score):
            raise ValueError("Scores cannot be NaN")


@dataclass(frozen=True)
class RoutingDecision:
    route_id: str
    selected_profile: str
    selected_tier: str
    context_profile: Optional[str]
    reasons: Tuple[str, ...]
    component_scores: Dict[str, float]
    escalation_conditions: Tuple[str, ...]
    privacy_mode: str
    privacy_decision: str
    policy_version: str
    created_at: str

    def __post_init__(self):
        if self.privacy_mode not in PRIVACY_MODES:
            raise ValueError(f"Invalid privacy mode {self.privacy_mode!r}")


@dataclass(frozen=True)
class ExecutionHandoff:
    original_task: str
    verified_facts: Tuple[str, ...]
    selected_sources: Tuple[Dict, ...]
    tool_results: Tuple[Dict, ...]
    errors: Tuple[str, ...]
    validation_results: Tuple[Dict, ...]
    pending_actions: Tuple[str, ...]
    input_tokens_spent: int
    output_tokens_spent: int
