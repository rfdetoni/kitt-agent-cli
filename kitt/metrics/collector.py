from typing import List, Dict, Any, Optional
from kitt.metrics.models import TurnMetrics

class MetricsCollector:
    """Collects and aggregates token savings and performance telemetry."""

    def __init__(self):
        self.history: List[TurnMetrics] = []

    def record_turn(self, metrics: TurnMetrics):
        self.history.append(metrics)

    def get_summary(self) -> Dict[str, Any]:
        if not self.history:
            return {
                "total_turns": 0,
                "gross_saved_total": 0,
                "net_saved_total": 0,
                "avg_net_saved_pct": 0.0
            }

        gross_total = sum(m.gross_saved for m in self.history)
        net_total = sum(m.net_saved for m in self.history)
        avg_pct = sum(m.net_saved_pct for m in self.history) / len(self.history)

        return {
            "total_turns": len(self.history),
            "gross_saved_total": gross_total,
            "net_saved_total": net_total,
            "avg_net_saved_pct": round(avg_pct, 1)
        }
