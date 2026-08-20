from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

AutonomyLevel = Literal["read_only", "supervised", "balanced", "autonomous"]


@dataclass(frozen=True)
class AutonomyPolicy:
    """Controls automatic actions without overriding PolicyEngine DENY rules."""

    level: AutonomyLevel = "supervised"
    allow_file_write_auto: bool = False
    allow_run_command_auto: bool = False
    allow_child_spawn_auto: bool = True
    max_auto_actions_per_turn: int = 20

    @classmethod
    def preset(cls, level: AutonomyLevel | str) -> "AutonomyPolicy":
        presets = {
            "read_only": cls("read_only", False, False, False, 0),
            "supervised": cls("supervised", False, False, True, 20),
            "balanced": cls("balanced", True, False, True, 20),
            "autonomous": cls("autonomous", True, True, True, 40),
        }
        alias_map = {
            "files_free": "balanced",
            "always_allow": "balanced",
        }
        target = alias_map.get(str(level), str(level))
        if target not in presets:
            raise ValueError(
                f"Nível de autonomia desconhecido: {level!r}. "
                f"Válidos: {sorted(presets)}"
            )
        return presets[target]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AutonomyPolicy":
        if not isinstance(data, dict):
            raise ValueError("Autonomy policy must be an object")
        base = cls.preset(data.get("level", "supervised"))

        def boolean(name: str, default: bool) -> bool:
            value = data.get(name, default)
            if not isinstance(value, bool):
                raise ValueError(f"{name} must be boolean")
            return value

        max_actions = int(
            data.get("max_auto_actions_per_turn", base.max_auto_actions_per_turn)
        )
        if not 0 <= max_actions <= 1000:
            raise ValueError("max_auto_actions_per_turn must be between 0 and 1000")

        return cls(
            level=base.level,
            allow_file_write_auto=boolean(
                "allow_file_write_auto", base.allow_file_write_auto
            ),
            allow_run_command_auto=boolean(
                "allow_run_command_auto", base.allow_run_command_auto
            ),
            allow_child_spawn_auto=boolean(
                "allow_child_spawn_auto", base.allow_child_spawn_auto
            ),
            max_auto_actions_per_turn=max_actions,
        )
