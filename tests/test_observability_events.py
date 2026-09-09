from __future__ import annotations

from kitt.core.event_bus import EventBus
from kitt.observability.event_observer import OpenTelemetryEventObserver, build_default_observer


class _Span:
    def __init__(self):
        self.attributes = {}

    def set_attribute(self, key, value):
        self.attributes[key] = value


class _SpanContext:
    def __init__(self, span):
        self.span = span

    def __enter__(self):
        return self.span

    def __exit__(self, exc_type, exc, tb):
        return False


class _Tracer:
    def __init__(self):
        self.names = []
        self.spans = []

    def start_as_current_span(self, name):
        span = _Span()
        self.names.append(name)
        self.spans.append(span)
        return _SpanContext(span)


class _Observer:
    def __init__(self):
        self.events = []
        self.closed = False

    def observe(self, event, payload):
        self.events.append((event, payload))

    def close(self):
        self.closed = True


def test_otel_observer_redacts_sensitive_payload_fields():
    tracer = _Tracer()
    observer = OpenTelemetryEventObserver(tracer)
    observer.observe("ToolCompleted", {"tool_name": "search", "api_key": "super-secret", "duration_ms": 12})
    attrs = tracer.spans[0].attributes
    assert attrs["kitt.event.name"] == "ToolCompleted"
    assert "super-secret" not in repr(attrs)


def test_event_bus_observer_is_optional_and_lifecycle_bound():
    observer = _Observer()
    bus = EventBus(observer=observer)
    seen = []
    bus.subscribe("x", lambda event, payload: seen.append((event, payload)))
    bus.publish("x", {"value": 1})
    assert observer.events == [("x", {"value": 1})]
    assert seen == [("x", {"value": 1})]
    bus.close()
    assert observer.closed is True


def test_default_observer_disabled_without_env(monkeypatch):
    monkeypatch.delenv("KITT_OTEL_ENABLED", raising=False)
    assert build_default_observer() is None
