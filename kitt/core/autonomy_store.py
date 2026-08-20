from __future__ import annotations

import json
from pathlib import Path

from kitt.core.autonomy_policy import AutonomyPolicy


class AutonomyStore:
    _UPDATABLE_FIELDS = frozenset({
        "level",
        "allow_file_write_auto",
        "allow_run_command_auto",
        "allow_child_spawn_auto",
        "max_auto_actions_per_turn",
    })

    def __init__(self, root_dir: str, persistence_enabled: bool = True):
        self.path = Path(root_dir) / ".kitt" / "config" / "autonomy.json"
        self.persistence_enabled = persistence_enabled
        self._current = self._load()

    def _load(self) -> AutonomyPolicy:
        if self.persistence_enabled and self.path.exists():
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                return AutonomyPolicy.from_dict(payload)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pass
        return AutonomyPolicy.preset("supervised")

    def get(self) -> AutonomyPolicy:
        return self._current

    def _persist(self) -> None:
        if not self.persistence_enabled:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._current.to_dict(), indent=2),
            encoding="utf-8",
        )

    def set_level(self, level: str) -> AutonomyPolicy:
        self._current = AutonomyPolicy.preset(level)
        self._persist()
        return self._current

    def set_preset(self, preset: str) -> AutonomyPolicy:
        return self.set_level(preset)

    def update(self, **kwargs) -> AutonomyPolicy:
        unknown = set(kwargs) - self._UPDATABLE_FIELDS
        if unknown:
            raise ValueError(f"Unknown autonomy fields: {sorted(unknown)}")
        data = self._current.to_dict()
        data.update(kwargs)
        self._current = AutonomyPolicy.from_dict(data)
        self._persist()
        return self._current
