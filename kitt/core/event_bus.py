from collections import defaultdict
from threading import RLock


class EventBus:
    def __init__(self):
        self._handlers = defaultdict(list)
        self._unsubscribers = defaultdict(list)
        self._lock = RLock()
        self._closed = False

    def subscribe(self, event, handler):
        with self._lock:
            if self._closed:
                raise RuntimeError("EventBus is closed; cannot subscribe.")
            self._handlers[event].append(handler)

        def unsubscribe():
            with self._lock:
                if handler in self._handlers.get(event, ()):
                    self._handlers[event].remove(handler)

        with self._lock:
            self._unsubscribers[event].append(unsubscribe)
        return unsubscribe

    def unsubscribe(self, event, handler):
        with self._lock:
            if handler in self._handlers[event]:
                self._handlers[event].remove(handler)

    def publish(self, event, payload):
        """Publish an event to all matching subscribers synchronously.

        Handlers are copied under lock and invoked outside the lock to prevent deadlocks.
        """
        with self._lock:
            if self._closed:
                return
            handlers = list(self._handlers.get(event, ())) + list(self._handlers.get("*", ()))
        for handler in handlers:
            handler(event, payload)

    async def async_publish(self, event, payload):
        """Publish an event asynchronously, awaiting coroutine handlers if present."""
        import inspect
        with self._lock:
            if self._closed:
                return
            handlers = list(self._handlers.get(event, ())) + list(self._handlers.get("*", ()))
        for handler in handlers:
            if inspect.iscoroutinefunction(handler):
                await handler(event, payload)
            else:
                handler(event, payload)

    def close(self):
        """Clear handlers, run unsubscribers, and refuse further publishing.

        Idempotent and safe to call more than once.
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            unsubscribers = list(self._unsubscribers.values())
            self._handlers.clear()
            self._unsubscribers.clear()
        for group in unsubscribers:
            for fn in group:
                try:
                    fn()
                except Exception:
                    pass
