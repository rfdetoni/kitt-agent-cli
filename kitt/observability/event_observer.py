from __future__ import annotations

import os
from typing import Any

from kitt.security.public_events import sanitize_public_event_payload

_TRUE = {"1", "true", "yes", "on", "enabled"}


class OpenTelemetryEventObserver:
    """Emit sanitized instantaneous KITT event spans to an existing OTel provider."""

    def __init__(self, tracer: Any):
        self.tracer = tracer

    @staticmethod
    def _attributes(event: str, payload: Any) -> dict[str, str | int | float | bool]:
        sanitized = sanitize_public_event_payload(event, payload if isinstance(payload, dict) else {})
        attrs: dict[str, str | int | float | bool] = {"kitt.event.name": str(event)[:128]}
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
            return

    def close(self) -> None:
        return None


def build_default_observer():
    """Build all explicitly enabled telemetry sinks as one fail-open observer."""
    otel_enabled = os.getenv("KITT_OTEL_ENABLED", "").strip().lower() in _TRUE
    langfuse_enabled = os.getenv("KITT_LANGFUSE_ENABLED", "").strip().lower() in _TRUE
    if not otel_enabled and not langfuse_enabled:
        return None
    try:
        from opentelemetry import trace
    except (ImportError, ModuleNotFoundError):
        return None

    observers: list[Any] = []
    try:
        if otel_enabled:
            observers.append(OpenTelemetryEventObserver(trace.get_tracer("kitt.runtime.events")))
        if langfuse_enabled:
            from kitt.observability.langfuse import LangfuseEventObserver
            observers.append(LangfuseEventObserver(trace.get_tracer("kitt.langfuse.events")))
    except Exception:
        pass

    if not observers:
        return None
    if len(observers) == 1:
        return observers[0]
    from kitt.observability.composite import CompositeEventObserver
    return CompositeEventObserver(observers)
