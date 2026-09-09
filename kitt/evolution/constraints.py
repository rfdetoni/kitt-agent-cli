"""Fail-closed constraints for evolved SKILL.md candidates."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


_FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)


@dataclass(frozen=True)
class SkillParts:
    frontmatter: str
    body: str


def split_skill(content: str) -> SkillParts:
    match = _FRONT.match(content.strip())
    if not match:
        raise ValueError("skill must have YAML frontmatter and markdown body")
    return SkillParts(match.group(1).strip(), match.group(2).strip())


def reassemble_skill(parts: SkillParts, body: str) -> str:
    return f"---\n{parts.frontmatter}\n---\n\n{body.strip()}\n"


def skill_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class SkillConstraintValidator:
    def __init__(
        self,
        *,
        max_bytes: int = 15 * 1024,
        max_growth_ratio: float = 0.25,
        min_body_chars: int = 80,
    ):
        self.max_bytes = max(1024, int(max_bytes))
        self.max_growth_ratio = max(0.0, float(max_growth_ratio))
        self.min_body_chars = max(20, int(min_body_chars))

    def validate(self, baseline: str, candidate: str) -> list[dict]:
        try:
            base = split_skill(baseline)
            evolved = split_skill(candidate)
        except ValueError as exc:
            return [{"name": "structure", "passed": False, "message": str(exc)}]

        size = len(candidate.encode("utf-8"))
        baseline_body_bytes = len(base.body.encode("utf-8"))
        candidate_body_bytes = len(evolved.body.encode("utf-8"))
        growth = (candidate_body_bytes - baseline_body_bytes) / max(1, baseline_body_bytes)

        return [
            {
                "name": "frontmatter_preserved",
                "passed": evolved.frontmatter == base.frontmatter,
                "message": "frontmatter must remain unchanged",
            },
            {
                "name": "size",
                "passed": size <= self.max_bytes,
                "message": f"{size}/{self.max_bytes} bytes",
            },
            {
                "name": "growth",
                "passed": growth <= self.max_growth_ratio,
                "message": f"{growth:+.1%} <= {self.max_growth_ratio:+.1%}",
            },
            {
                "name": "non_empty",
                "passed": len(evolved.body.strip()) >= self.min_body_chars,
                "message": f"body chars={len(evolved.body.strip())}",
            },
        ]
