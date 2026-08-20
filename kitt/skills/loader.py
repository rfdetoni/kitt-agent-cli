import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, List

from kitt.skills.skill_manager import DEFAULT_SKILLS


class ProgressiveSkillLoader:
    def select(self, skills: List[Any], prompt: str, max_skills: int = 3) -> List[Any]:
        if not skills or not prompt:
            return []

        prompt_lower = prompt.lower()
        prompt_words = re.findall(r"[a-z0-9_+-]{2,}", prompt_lower)
        prompt_word_set = set(prompt_words)

        num_docs = len(skills)
        df: Counter = Counter()
        skill_terms_map = {}
        for s in skills:
            terms = set(re.findall(r"[a-z0-9_+-]{2,}", f"{s.name} {s.description}".lower()))
            skill_terms_map[s.name] = terms
            for t in terms:
                df[t] += 1

        scored = []
        for skill in skills:
            score = 0.0
            s_name = skill.name.lower()

            if s_name in prompt_lower or f"/{s_name}" in prompt_lower or f"@{s_name}" in prompt_lower:
                score += 100.0

            terms = skill_terms_map.get(skill.name, set())
            common_words = prompt_word_set.intersection(terms)
            for w in common_words:
                idf = math.log((num_docs + 1.0) / (df[w] + 0.5)) + 1.0
                score += idf
                if w in s_name:
                    score += 2.0 * idf

            if score > 0:
                scored.append((score, skill.name, skill))

        scored.sort(key=lambda item: (-item[0], item[1]))
        return [item[2] for item in scored[:max_skills]]

    def load(self, skill: Any, max_chars: int = 12000) -> str:
        skill_path = Path(getattr(skill, "path", Path(".")))
        skill_md = skill_path / "SKILL.md"
        if skill_md.exists():
            text = skill_md.read_text("utf-8", errors="ignore")
        else:
            text = DEFAULT_SKILLS.get(
                getattr(skill, "name", ""),
                {},
            ).get(
                "content",
                f"---\nname: {getattr(skill, 'name', 'unknown')}\ndescription: {getattr(skill, 'description', '')}\n---\n",
            )
        return text[:max_chars]
