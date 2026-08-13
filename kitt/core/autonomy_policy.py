from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal

AutonomyLevel = Literal["read_only", "supervised", "balanced", "autonomous"]

@dataclass(frozen=True)
class AutonomyPolicy:
    """Controla o quanto o agente pode agir sem pedir confirmação.

    IMPORTANTE: esta política NUNCA sobrepõe as regras de DENY absoluto do
    PolicyEngine (shell operators, comandos destrutivos, git push/reset,
    fuga de path do workspace). Ela só decide o que vira ASK vs ALLOW
    dentro do espaço já considerado seguro pelo PolicyEngine.
    """
    level: AutonomyLevel = "supervised"
    allow_file_write_auto: bool = False     # apply_patch / write_file sem pedir aprovação
    allow_run_command_auto: bool = False    # run_command (subconjunto não-destrutivo) sem aprovação
    allow_child_spawn_auto: bool = True     # spawn de agentes filho (já sandboxed) sem aprovação
    max_auto_actions_per_turn: int = 20     # trava de segurança mesmo em modo autonomous

    @classmethod
    def preset(cls, level: AutonomyLevel | str) -> AutonomyPolicy:
        presets = {
            "read_only":  cls("read_only",  False, False, False, 0),
            "supervised": cls("supervised", False, False, True, 20),
            "balanced":   cls("balanced",   True,  False, True, 20),
            "autonomous": cls("autonomous", True,  True,  True, 40),
        }
        alias_map = {
            "files_free": "balanced",
            "always_allow": "balanced",
        }
        target = alias_map.get(level, level)
        if target not in presets:
            raise ValueError(f"Nível de autonomia desconhecido: {level!r}. Válidos: {sorted(presets)}")
        return presets[target]

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> AutonomyPolicy:
        level = data.get("level", "supervised")
        return cls.preset(level)
