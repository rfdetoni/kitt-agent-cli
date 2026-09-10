from __future__ import annotations

import os
from threading import RLock
from typing import Any

from kitt.security.public_events import sanitize_public_event_payload

_TRUE = {"1", "true", "yes", "on", "enabled"}
_TERMINAL = {"COMPLETED", "FAILED", "BLOCKED", "CANCELLED"}


class OpenTelemetryEventObserver:
    """Emit sanitized hierarchical KITT traces when correlation is available.

    ``TurnStateChanged`` events create one durable root span per turn. Other
    events carrying the same turn id are emitted beneath that span. If tracing
    is partially configured or a provider rejects a span, observation remains
    fail-open and never affects agent execution.
    """

    def __init__(self, tracer: Any):
        self.tracer = tracer
        self._turn_spans: dict[str, Any] = {}
        self._lock = RLock()

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

    def _child_context(self, turn_id: str):
        if not turn_id:
            return None
        with self._lock:
            parent = self._turn_spans.get(turn_id)
        if parent is None:
            return None
        try:
            from opentelemetry import trace
            return trace.set_span_in_context(parent)
        except Exception:
            return None

    def observe(self, event: str, payload: Any) -> None:
        try:
            safe_payload = payload if isinstance(payload, dict) else {}
            attrs = self._attributes(event, safe_payload)
            turn_id = str(safe_payload.get("turn_id") or "")[:128]
            state = str(safe_payload.get("state") or "").upper()

            if event == "TurnStateChanged" and turn_id:
                with self._lock:
                    root = self._turn_spans.get(turn_id)
                    if root is None and state == "RUNNING":
                        root = self.tracer.start_span("kitt.turn")
                        root.set_attribute("kitt.turn.id", turn_id)
                        conversation_id = str(safe_payload.get("conversation_id") or "")
                        if conversation_id:
                            root.set_attribute("kitt.conversation.id", conversation_id[:128])
                        self._turn_spans[turn_id] = root
                context = self._child_context(turn_id)
                phase = self.tracer.start_span(f"kitt.phase.{state.lower() or 'unknown'}", context=context)
                for key, value in attrs.items():
                    phase.set_attribute(key, value)
                phase.end()
                if state in _TERMINAL:
                    with self._lock:
                        root = self._turn_spans.pop(turn_id, None)
                    if root is not None:
                        root.set_attribute("kitt.turn.state", state)
                        root.end()
                return

            context = self._child_context(turn_id)
            span = self.tracer.start_span(f"kitt.event.{str(event)[:96]}", context=context)
            for key, value in attrs.items():
                span.set_attribute(key, value)
            span.end()
        except Exception:
            return

    def close(self) -> None:
        with self._lock:
            spans = list(self._turn_spans.values())
            self._turn_spans.clear()
        for span in spans:
            try:
                span.set_attribute("kitt.turn.state", "OBSERVER_CLOSED")
                span.end()
            except Exception:
                pass


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
