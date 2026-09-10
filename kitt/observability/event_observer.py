from __future__ import annotations

import os
from typing import Any

from kitt.security.public_events import sanitize_public_event_payload


class OpenTelemetryEventObserver:
    """Emit sanitized instantaneous KITT event spans to an existing OTel provider.

    KITT never configures an exporter here. If the host application configured
    an OpenTelemetry SDK/provider, these spans flow through it; otherwise the
    OpenTelemetry API's no-op provider keeps this path effectively free.
    """

    def __init__(self, tracer: Any):
        self.tracer = tracer

    @staticmethod
    def _attributes(event: str, payload: Any) -> dict[str, str | int | float | bool]:
        sanitized = sanitize_public_event_payload(event, payload if isinstance(payload, dict) else {})
        attrs: dict[str, str | int | float | bool] = {
            "kitt.event.name": str(event)[:128],
        }
        for key, value in sanitized.items():
            if isinstance(value, bool):
                attrs[f"kitt.event.{key}"] = value
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                attrs[f"kitt.event.{key}"] = value
            elif isinstance(value, str):
                attrs[f"kitt.event.{key}"] = value[:512]
        return attrs

    def observe(self, event: str, payload: Any) -> None:
        try:
            with self.tracer.start_as_current_span(f"kitt.event.{str(event)[:96]}") as span:
                for key, value in self._attributes(event, payload).items():
                    span.set_attribute(key, value)
        except Exception:
            # Observability is deliberately fail-open with respect to telemetry:
            # it must never take the runtime down or alter tool/model results.
            return

    def close(self) -> None:
        return None


def build_default_observer() -> OpenTelemetryEventObserver | None:
    enabled = os.getenv("KITT_OTEL_ENABLED", "").strip().lower() in {
        "1", "true", "yes", "on", "enabled"
    }
    if not enabled:
        return None
    try:
        from opentelemetry import trace
    except (ImportError, ModuleNotFoundError):
        return None
    try:
        return OpenTelemetryEventObserver(trace.get_tracer("kitt.runtime.events"))
    except Exception:
        return None
