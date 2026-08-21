from __future__ import annotations

import threading
from pathlib import Path

_LOCKS_GUARD = threading.Lock()
_WORKSPACE_LOCKS: dict[str, threading.RLock] = {}


def workspace_mutation_lock(root_dir: str | Path) -> threading.RLock:
    key = str(Path(root_dir).expanduser().resolve())
    with _LOCKS_GUARD:
        lock = _WORKSPACE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _WORKSPACE_LOCKS[key] = lock
        return lock
