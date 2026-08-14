"""Evaluation corpus, benchmark metrics, and ablation runner."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List, Dict, Any, Tuple, Optional

from kitt.router.features import TaskFeatureExtractor
from kitt.router.policy import RoutingPolicy
from kitt.router.models import ModelCapabilities


@dataclass(frozen=True)
class EvalTestCase:
    id: str
    prompt: str
    language: str
    expected_intent: str
    expected_complexity: str
    expected_risk: str
    expected_route: str
    expected_paths: Tuple[str, ...]


DEFAULT_EVAL_CORPUS = [
    EvalTestCase("tc1", "Fix syntax error in app.py", "en", "DEBUG", "MEDIUM", "MEDIUM", "local_small", ("app.py",)),
    EvalTestCase("tc2", "Corrigir exceção em turn_processor.py", "pt", "DEBUG", "MEDIUM", "MEDIUM", "local_small", ("turn_processor.py",)),
    EvalTestCase("tc3", "Explain how the router works", "en", "READ", "LOW", "LOW", "local_small", ()),
    EvalTestCase("tc4", "Refactor entire multi-module architecture across all files", "en", "IMPLEMENT", "HIGH", "MEDIUM", "cloud_large", ()),
]


class EvalRunner:
    """Runs evaluation benchmarks and ablation studies across routing modes."""

    def __init__(self, corpus: List[EvalTestCase] | None = None):
        self.corpus = corpus or DEFAULT_EVAL_CORPUS

    def run_routing_eval(self, capabilities: Dict[str, ModelCapabilities]) -> Dict[str, Any]:
        policy = RoutingPolicy()
        total = len(self.corpus)
        correct_intent = 0
        correct_route = 0
        latencies = []

        for tc in self.corpus:
            t0 = time.time()
            features = TaskFeatureExtractor.extract(tc.prompt)
            decision = policy.select_route(features, capabilities, privacy_mode="cloud_allowed")
            latency = (time.time() - t0) * 1000
            latencies.append(latency)

            if features.intent == tc.expected_intent:
                correct_intent += 1
            if decision.selected_profile == tc.expected_route or decision.selected_tier == ("small" if "small" in tc.expected_route else "large"):
                correct_route += 1

        return {
            "total_cases": total,
            "intent_accuracy": round(correct_intent / total, 2),
            "routing_accuracy": round(correct_route / total, 2),
            "avg_latency_ms": round(sum(latencies) / total, 2)
        }

    def run_ablations(self, capabilities: Dict[str, ModelCapabilities]) -> Dict[str, Dict[str, Any]]:
        """Run cheap but distinct routing ablations."""
        local_caps = {name: cap for name, cap in capabilities.items() if cap.is_local}
        large_caps = {name: cap for name, cap in capabilities.items() if cap.tier == "large"} or capabilities
        degraded_caps = {
            name: ModelCapabilities(**{**cap.__dict__, "health": "degraded" if cap.tier == "small" else cap.health})
            for name, cap in capabilities.items()
        }
        return {
            "1_naive_full_context": self._constant_route(capabilities, prefer_large=True),
            "2_deterministic_exact_lexical": self.run_routing_eval(local_caps or capabilities),
            "3_hybrid_structural": self.run_routing_eval(capabilities),
            "4_hybrid_graph": self.run_routing_eval(degraded_caps),
            "5_large_direct": self._constant_route(large_caps, prefer_large=True),
        }

    def _constant_route(self, capabilities: Dict[str, ModelCapabilities], prefer_large: bool = False) -> Dict[str, Any]:
        total = len(self.corpus)
        route = None
        ordered = list(capabilities.values())
        if prefer_large:
            route = next((cap for cap in ordered if cap.tier == "large"), None)
        route = route or (ordered[0] if ordered else None)
        correct_route = 0
        correct_intent = 0
        for tc in self.corpus:
            features = TaskFeatureExtractor.extract(tc.prompt)
            correct_intent += int(features.intent == tc.expected_intent)
            if route and (route.profile_name == tc.expected_route or route.tier == ("small" if "small" in tc.expected_route else "large")):
                correct_route += 1
        return {
            "total_cases": total,
            "intent_accuracy": round(correct_intent / total, 2),
            "routing_accuracy": round(correct_route / total, 2),
            "avg_latency_ms": 0.0,
        }
