"""Optional observability sinks for K.I.T.T. runtime events."""

from .event_observer import OpenTelemetryEventObserver, build_default_observer

__all__ = ["OpenTelemetryEventObserver", "build_default_observer"]
