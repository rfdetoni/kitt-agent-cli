from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock
from typing import List, Dict, Any

from kitt.metrics.models import ToolGainMetrics, TurnMetrics


class MetricsCollector:
    """Collects turn telemetry and RTK-style KITT tool-output gain metrics."""

    MAX_TOOL_GAIN_HISTORY = 10_000

    def __init__(self, repository=None):
        self.history: List[TurnMetrics] = []
        self.tool_gain_history: List[ToolGainMetrics] = []
        self.repository = repository
        self._lock = Lock()
        self._recorded_turn_ids: set = set()
        self.rejected_duplicates: int = 0
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kitt-metrics")
        self._closed = False
        self._last_future: Future | None = None

    def record(self, payload: Any):
        if isinstance(payload, TurnMetrics):
            self.record_turn(payload)
        elif isinstance(payload, dict):
            m = TurnMetrics(
                turn_id=payload.get("turn_id", "default"),
                conversation_id=payload.get("conversation_id", "default"),
                route=payload.get("route", "code-generation"),
                naive_input_tokens=payload.get("naive_input_tokens", 0),
                actual_input_tokens=payload.get("input_tokens", payload.get("actual_input_tokens", 0)),
                actual_output_tokens=payload.get("output_tokens", payload.get("actual_output_tokens", 0)),
                duration_ms=payload.get("duration_ms", 0.0),
            )
            self.record_turn(m)

    def _submit(self, function, *args) -> None:
        if self._closed:
            return
        self._last_future = self._executor.submit(function, *args)

    def record_turn(self, metrics: TurnMetrics):
        with self._lock:
            if metrics.turn_id and metrics.turn_id != "default" and metrics.turn_id in self._recorded_turn_ids:
                self.rejected_duplicates += 1
                return
            if metrics.turn_id:
                self._recorded_turn_ids.add(metrics.turn_id)
            self.history.append(metrics)
        if self.repository:
            self._submit(
                self.repository.save_telemetry,
                metrics.conversation_id,
                metrics.turn_id,
                metrics.route,
                metrics.timestamp,
                metrics.duration_ms,
                metrics.actual_input_tokens,
                metrics.actual_output_tokens,
                metrics.net_saved,
            )

    def record_tool_gain(self, metrics: ToolGainMetrics) -> None:
        with self._lock:
            self.tool_gain_history.append(metrics)
            overflow = len(self.tool_gain_history) - self.MAX_TOOL_GAIN_HISTORY
            if overflow > 0:
                del self.tool_gain_history[:overflow]
        if self.repository:
            self._submit(
                self.repository.save_tool_gain,
                metrics.conversation_id,
                metrics.turn_id,
                metrics.tool_name,
                metrics.timestamp,
                metrics.duration_ms,
                metrics.raw_tokens,
                metrics.output_tokens,
                metrics.tokens_saved,
            )

    def flush(self) -> None:
        """Wait until all metrics queued before this call are persisted."""
        if self._closed:
            return
        barrier = self._executor.submit(lambda: None)
        barrier.result()

    def get_summary(self) -> Dict[str, Any]:
        with self._lock:
            snapshot = list(self.history)
        if not snapshot:
            return {
                "total_turns": 0,
                "gross_saved_total": 0,
                "net_saved_total": 0,
                "avg_net_saved_pct": 0.0
            }

        gross_total = sum(m.gross_saved for m in snapshot)
        net_total = sum(m.net_saved for m in snapshot)
        avg_pct = sum(m.net_saved_pct for m in snapshot) / len(snapshot)

        return {
            "total_turns": len(snapshot),
            "gross_saved_total": gross_total,
            "net_saved_total": net_total,
            "avg_net_saved_pct": round(avg_pct, 1)
        }

    def close(self):
        if not self._closed:
            self._executor.shutdown(wait=True, cancel_futures=False)
            self._closed = True
