import json
from pathlib import Path
from kitt.core.autonomy_policy import AutonomyPolicy, AutonomyLevel

class AutonomyStore:
    def __init__(self, root_dir: str, persistence_enabled: bool = True):
        self.path = Path(root_dir) / ".kitt" / "config" / "autonomy.json"
        self.persistence_enabled = persistence_enabled
        self._current = self._load()

    def _load(self) -> AutonomyPolicy:
        if self.persistence_enabled and self.path.exists():
            try:
                return AutonomyPolicy.from_dict(json.loads(self.path.read_text(encoding="utf-8")))
            except Exception:
                pass
        return AutonomyPolicy.preset("supervised")

    def get(self) -> AutonomyPolicy:
        return self._current

    def set_level(self, level: str) -> AutonomyPolicy:
        self._current = AutonomyPolicy.preset(level) # type: ignore
        if self.persistence_enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._current.to_dict(), indent=2), encoding="utf-8")
        return self._current

    def set_preset(self, preset: str) -> AutonomyPolicy:
        return self.set_level(preset)

    def update(self, **kwargs) -> AutonomyPolicy:
        data = self._current.to_dict()
        data.update(kwargs)
        self._current = AutonomyPolicy.from_dict(data)
        if self.persistence_enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._current.to_dict(), indent=2), encoding="utf-8")
        return self._current
