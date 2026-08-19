from __future__ import annotations

import json
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any


class PrimeMetrics:
    """Low-overhead runtime metrics sink for Prime Architecture events."""

    def __init__(self, root_dir: str | Path):
        self.root = Path(root_dir).resolve()
        self._lock = threading.RLock()
        self._counters = defaultdict(int)
        self._sums = defaultdict(float)
        self._last_event_at = 0.0

    def inc(self, name: str, value: int = 1) -> None:
        with self._lock:
            self._counters[name] += value

    def observe_value(self, name: str, value: float) -> None:
        with self._lock:
            self._counters[f"{name}.count"] += 1
            self._sums[f"{name}.sum"] += float(value)

    def observe(self, event: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._last_event_at = time.time()
        mapping = {
            "ChildAgentSpawned": "agent_spawn_total",
            "ChildAgentRetained": "agent_reuse_total",
            "ChildAgentMessageSent": "agent_messages_total",
            "ChildAgentFinished": "agent_finished_total",
            "GoalSchedulerRun": "scheduler_runs_total",
            "GoalSchedulerFailure": "scheduler_failures_total",
            "GoalSchedulerRetry": "scheduler_retries_total",
            "SessionAttached": "session_attach_total",
            "SessionDetached": "session_detach_total",
            "SessionReconnect": "session_reconnect_total",
        }
        if event in mapping:
            self.inc(mapping[event])
        if event == "RuntimeOperation":
            self.inc("runtime_operations_total")
            if not payload.get("success", False):
                self.inc("runtime_operation_failures_total")
            self.observe_value("runtime_operation_latency_ms", float(payload.get("duration_ms", 0.0)))
        if event == "PolicyDecision":
            decision = str(payload.get("decision", "")).lower()
            if decision in {"allow", "deny", "ask"}:
                self.inc(f"policy_{decision}_total")
        if event == "SkillExecution":
            self.inc("skill_executions_total")
            if not payload.get("success", False):
                self.inc("skill_failures_total")
            if payload.get("timed_out"):
                self.inc("skill_timeouts_total")

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "counters": dict(self._counters),
                "sums": dict(self._sums),
                "last_event_at": self._last_event_at,
            }

    def flush(self) -> Path:
        target = self.root / ".kitt" / "metrics" / "prime.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.snapshot(), indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(target)
        return target
