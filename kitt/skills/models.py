from dataclasses import dataclass
from pathlib import Path
@dataclass(frozen=True)
class SkillDescriptor:
    name:str; description:str; version:str; author:str; path:Path
