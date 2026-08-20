from __future__ import annotations

import threading
from pathlib import Path

from kitt.core.autonomy_policy import AutonomyPolicy
from kitt.security.private_state import (
    secure_read_json,
    secure_write_json,
    workspace_state_dir,
)


class AutonomyStore:
    """User-authorized autonomy state stored outside repository control."""

    _UPDATABLE_FIELDS = frozenset({
        "level",
        "allow_file_write_auto",
        "allow_run_command_auto",
        "allow_child_spawn_auto",
        "max_auto_actions_per_turn",
    })

    def __init__(self, root_dir: str, persistence_enabled: bool = True):
        self.root_dir = Path(root_dir).expanduser().resolve()
        self.persistence_enabled = persistence_enabled
        self.path = workspace_state_dir(self.root_dir, "config") / "autonomy.json"
        # Never migrate .kitt/config/autonomy.json automatically: repository
        # content is not an authorization source.
        self.workspace_suggestion_path = self.root_dir / ".kitt" / "config" / "autonomy.json"
        self._lock = threading.RLock()
        self._current = self._load()

    def _load(self) -> AutonomyPolicy:
        if not self.persistence_enabled:
            return AutonomyPolicy.preset("supervised")
        payload = secure_read_json(self.path, default=None, max_bytes=64 * 1024)
        if payload is None:
            return AutonomyPolicy.preset("supervised")
        try:
            return AutonomyPolicy.from_dict(payload)
        except (ValueError, TypeError):
            return AutonomyPolicy.preset("supervised")

    def get(self) -> AutonomyPolicy:
        with self._lock:
            return self._current

    def _persist(self) -> None:
        if not self.persistence_enabled:
            return
        secure_write_json(self.path, self._current.to_dict(), max_bytes=64 * 1024)

    def set_level(self, level: str) -> AutonomyPolicy:
        with self._lock:
            self._current = AutonomyPolicy.preset(level)
            self._persist()
            return self._current

    def set_preset(self, preset: str) -> AutonomyPolicy:
        return self.set_level(preset)

    def update(self, **kwargs) -> AutonomyPolicy:
        unknown = set(kwargs) - self._UPDATABLE_FIELDS
        if unknown:
            raise ValueError(f"Unknown autonomy fields: {sorted(unknown)}")
        with self._lock:
            data = self._current.to_dict()
            data.update(kwargs)
            self._current = AutonomyPolicy.from_dict(data)
            self._persist()
            return self._current
