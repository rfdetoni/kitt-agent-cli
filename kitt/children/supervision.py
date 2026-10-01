"""POSIX parent-loss containment for a leaf worker and its active tool runners."""

from __future__ import annotations

import os
import logging
import signal
import threading


class ParentProcessGuard:
    def __init__(self, parent_pid: int, cancel_active):
        if os.name != "posix" or parent_pid <= 1:
            raise ValueError("ParentProcessGuard requires a POSIX parent identity")
        self.parent_pid = parent_pid
        self.cancel_active = cancel_active
        self.stop = threading.Event()
        self.terminate = threading.Event()
        self.previous_handler = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, lambda *_: self.terminate.set())
        self.thread = threading.Thread(target=self._watch, name="kitt-parent-guard", daemon=True)
        self.thread.start()

    def _watch(self):
        while not self.stop.wait(0.1):
            # Reparenting proves loss of the original parent without killing a
            # possibly reused parent PID. Worker subprocesses are session leaders.
            if self.terminate.is_set() or os.getppid() != self.parent_pid:

                def cleanup():
                    try:
                        self.cancel_active()
                    except Exception:
                        logging.getLogger(__name__).exception("Parent-loss cleanup failed")

                threading.Thread(target=cleanup, name="kitt-parent-cleanup", daemon=True).start()
                # ProcessRunner observes cancellation every 20 ms and kills its
                # separate process group. Give it time before ending the worker.
                if not self.stop.wait(2.0):
                    os._exit(70)
                return

    def close(self):
        self.stop.set()
        self.thread.join(timeout=0.5)
        signal.signal(signal.SIGTERM, self.previous_handler)
