import threading
class CancellationToken:
    def __init__(self): self._event=threading.Event()
    def cancel(self): self._event.set()
    @property
    def cancelled(self): return self._event.is_set()
    def raise_if_cancelled(self):
        if self.cancelled: raise CancelledError()
class CancelledError(RuntimeError): pass
