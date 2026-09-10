from __future__ import annotations

from typing import Any

from kitt.observability.event_observer import OpenTelemetryEventObserver


class LangfuseEventObserver(OpenTelemetryEventObserver):
    """Langfuse-compatible OpenTelemetry sink using sanitized KITT attributes."""

    @staticmethod
    def _attributes(event: str, payload: Any) -> dict[str, str | int | float | bool]:
        attrs = OpenTelemetryEventObserver._attributes(event, payload)
        attrs["kitt.telemetry.sink"] = "langfuse"
        return attrs
