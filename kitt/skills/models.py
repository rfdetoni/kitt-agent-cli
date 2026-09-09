from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Tuple


@dataclass(frozen=True)
class SkillDescriptor:
    """Metadata used by the lazy skill selector.

    The descriptor intentionally keeps routing metadata separate from the skill
    body. `selected_content` is populated only after a skill is selected for a
    turn, so callers do not need to inject every active SKILL.md into context.
    """

    name: str
    description: str
    version: str
    author: str
    path: Path
    source: str = "workspace"
    active: bool = True
    trusted: bool = True
    always_apply: bool = False
    priority: int = 0
    globs: Tuple[str, ...] = ()
    depends_on: Tuple[str, ...] = ()
    keywords: Tuple[str, ...] = ()
    body_hint: str = ""
    selected_content: str = ""
    relevance_score: float = 0.0
