from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from kitt.history.database import HistoryDatabase
from kitt.security.private_state import workspace_state_dir


class WorkspaceHistoryDatabase(HistoryDatabase):
    """History database physically isolated under ~/.kitt for one workspace.

    ``HistoryDatabase`` keeps its legacy root-relative behavior for explicit
    low-level callers and tests. Runtime composition uses this subclass so a
    project checkout never receives a generated ``.kitt`` directory.
    """

    def __init__(self, workspace_root: str, in_memory: bool = False):
        if in_memory:
            super().__init__(":memory:", in_memory=True)
            return

        self.in_memory = False
        self._mem_lock = threading.RLock()
        self.root_path = Path(workspace_root).expanduser().resolve(strict=False)
        self.kitt_dir = workspace_state_dir(self.root_path, "history")
        self.db_path = self.kitt_dir / "history.sqlite3"
        self._init_db()
