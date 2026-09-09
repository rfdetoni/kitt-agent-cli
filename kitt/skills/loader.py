from __future__ import annotations

import fnmatch
import math
import re
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable, List

from kitt.skills.skill_manager import DEFAULT_SKILLS


_WORD_RE = re.compile(r"[a-z0-9_+./#-]{2,}", re.I)
_PATH_RE = re.compile(r"(?:^|\s)([\w@.+-]+(?:/[\w@.+-]+)+\.[a-zA-Z0-9]{1,12})(?=$|\s|[,;:)])")
_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n?", re.S)
_CORE_ALWAYS = frozenset({"caveman", "ponytail"})


class ProgressiveSkillLoader:
    """Select and materialize only the skill instructions relevant to a turn.

    Selection is deterministic, dependency aware and bounded by one global
    instruction budget. A selected descriptor carries a compact excerpt so the
    existing TurnProcessor can keep calling ``load(skill)`` without eagerly
    injecting every active SKILL.md.
    """

    def __init__(self, *, max_total_chars: int = 16000, min_skill_chars: int = 1200):
        self.max_total_chars = max(2000, min(int(max_total_chars), 128000))
        self.min_skill_chars = max(256, min(int(min_skill_chars), 8000))

    @staticmethod
    def _terms(value: str) -> set[str]:
        return {term.lower() for term in _WORD_RE.findall(value or "")}

    @staticmethod
    def _paths(prompt: str) -> tuple[str, ...]:
        paths: list[str] = []
        for match in _PATH_RE.findall(prompt or ""):
            clean = match.strip().replace("\\", "/")
            if clean and clean not in paths:
                paths.append(clean)
        return tuple(paths[:32])

    @staticmethod
    def _content(skill: Any) -> str:
        skill_path = Path(getattr(skill, "path", Path(".")))
        skill_md = skill_path / "SKILL.md"
        if skill_md.exists() and skill_md.is_file() and not skill_md.is_symlink():
            try:
                return skill_md.read_text("utf-8", errors="ignore")[:256 * 1024]
            except OSError:
                pass
        return str(
            DEFAULT_SKILLS.get(getattr(skill, "name", ""), {}).get(
                "content",
                f"---\nname: {getattr(skill, 'name', 'unknown')}\n"
                f"description: {getattr(skill, 'description', '')}\n---\n",
            )
        )

    def _score(
        self,
        skill: Any,
        prompt_lower: str,
        prompt_terms: set[str],
        paths: tuple[str, ...],
        df: Counter,
        num_docs: int,
    ) -> float:
        name = str(getattr(skill, "name", "")).lower()
        if not getattr(skill, "active", True) or not getattr(skill, "trusted", True):
            return float("-inf")

        always = bool(getattr(skill, "always_apply", False)) or name in _CORE_ALWAYS
        score = 1000.0 if always else 0.0
        priority = max(-1000, min(1000, int(getattr(skill, "priority", 0) or 0)))
        score += priority / 100.0

        if name and (name in prompt_lower or f"/{name}" in prompt_lower or f"@{name}" in prompt_lower):
            score += 200.0

        globs = tuple(getattr(skill, "globs", ()) or ())
        if globs and paths:
            matches = sum(
                1
                for path in paths
                if any(fnmatch.fnmatch(path, pattern) for pattern in globs)
            )
            score += matches * 25.0

        indexed_text = " ".join(
            (
                name,
                str(getattr(skill, "description", "")),
                " ".join(getattr(skill, "keywords", ()) or ()),
                str(getattr(skill, "body_hint", "")),
            )
        )
        terms = self._terms(indexed_text)
        keywords = {str(item).lower() for item in (getattr(skill, "keywords", ()) or ())}
        for word in prompt_terms.intersection(terms):
            idf = math.log((num_docs + 1.0) / (df[word] + 0.5)) + 1.0
            score += idf
            if word in name:
                score += 2.0 * idf
            if word in keywords:
                score += 3.0 * idf
        return score

    @staticmethod
    def _sections(content: str) -> list[tuple[str, str]]:
        body = _FRONTMATTER_RE.sub("", content, count=1).strip()
        if not body:
            return []
        sections: list[tuple[str, str]] = []
        heading = "Overview"
        current: list[str] = []
        for line in body.splitlines():
            if re.match(r"^#{1,4}\s+", line):
                if current:
                    sections.append((heading, "\n".join(current).strip()))
                heading = re.sub(r"^#{1,4}\s+", "", line).strip()
                current = [line]
            else:
                current.append(line)
        if current:
            sections.append((heading, "\n".join(current).strip()))
        return [(title, text) for title, text in sections if text]

    def _excerpt(self, skill: Any, prompt: str, budget: int) -> str:
        content = self._content(skill)
        if len(content) <= budget:
            return content

        prompt_terms = self._terms(prompt)
        keywords = {str(item).lower() for item in (getattr(skill, "keywords", ()) or ())}
        scored: list[tuple[float, int, str]] = []
        for index, (title, text) in enumerate(self._sections(content)):
            terms = self._terms(f"{title} {text[:3000]}")
            overlap = len(prompt_terms.intersection(terms))
            keyword_overlap = len(keywords.intersection(terms))
            mandatory = 20 if any(
                marker in title.lower()
                for marker in ("mandatory", "safety", "constraint", "protocol", "rule")
            ) else 0
            score = mandatory + overlap * 3 + keyword_overlap * 5
            if index == 0:
                score += 2
            scored.append((float(score), index, text))

        scored.sort(key=lambda item: (-item[0], item[1]))
        selected: list[tuple[int, str]] = []
        used = 0
        for _, index, text in scored:
            if used >= budget:
                break
            remaining = budget - used
            if remaining < 128:
                break
            piece = text[:remaining]
            selected.append((index, piece))
            used += len(piece) + 2
        selected.sort(key=lambda item: item[0])

        header = (
            f"---\nname: {getattr(skill, 'name', 'unknown')}\n"
            f"description: {getattr(skill, 'description', '')}\n"
            "lazy_excerpt: true\n---\n"
        )
        body = "\n\n".join(text for _, text in selected)
        return (header + body)[:budget]

    def _dependency_order(
        self,
        chosen: Iterable[Any],
        by_name: dict[str, Any],
        max_items: int,
    ) -> list[Any]:
        ordered: list[Any] = []
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(skill: Any) -> None:
            name = str(getattr(skill, "name", ""))
            if not name or name in visited or len(ordered) >= max_items:
                return
            if name in visiting:
                return
            visiting.add(name)
            for dep_name in getattr(skill, "depends_on", ()) or ():
                dep = by_name.get(str(dep_name))
                if dep is not None and getattr(dep, "active", True) and getattr(dep, "trusted", True):
                    visit(dep)
            visiting.discard(name)
            visited.add(name)
            if len(ordered) < max_items:
                ordered.append(skill)

        for skill in chosen:
            visit(skill)
        return ordered

    def select(self, skills: List[Any], prompt: str, max_skills: int = 3) -> List[Any]:
        eligible = [
            skill
            for skill in skills
            if getattr(skill, "active", True) and getattr(skill, "trusted", True)
        ]
        if not eligible:
            return []

        max_skills = max(1, min(int(max_skills), 32))
        prompt_lower = (prompt or "").lower()
        prompt_terms = self._terms(prompt_lower)
        paths = self._paths(prompt or "")
        num_docs = len(eligible)

        df: Counter = Counter()
        for skill in eligible:
            indexed = " ".join(
                (
                    str(getattr(skill, "name", "")),
                    str(getattr(skill, "description", "")),
                    " ".join(getattr(skill, "keywords", ()) or ()),
                    str(getattr(skill, "body_hint", "")),
                )
            )
            for term in self._terms(indexed):
                df[term] += 1

        scored: list[tuple[float, int, str, Any]] = []
        for skill in eligible:
            score = self._score(skill, prompt_lower, prompt_terms, paths, df, num_docs)
            name = str(getattr(skill, "name", "")).lower()
            always = bool(getattr(skill, "always_apply", False)) or name in _CORE_ALWAYS
            if score > 0 or always:
                scored.append(
                    (
                        score,
                        int(getattr(skill, "priority", 0) or 0),
                        str(getattr(skill, "name", "")),
                        skill,
                    )
                )
        scored.sort(key=lambda item: (-item[0], -item[1], item[2]))

        by_name = {str(getattr(skill, "name", "")): skill for skill in eligible}
        # Dependencies are supporting instructions, not primary selections. Let
        # them expand the primary set without displacing the task-specific skill.
        dependency_cap = min(32, max_skills + 8)
        ordered = self._dependency_order(
            (item[3] for item in scored[:max_skills]),
            by_name,
            dependency_cap,
        )
        if not ordered:
            return []

        # One hard global budget. A per-skill minimum may influence ranking but
        # can never make the aggregate exceed max_total_chars.
        remaining = self.max_total_chars
        materialized: list[Any] = []
        score_by_name = {item[2]: item[0] for item in scored}
        for index, skill in enumerate(ordered):
            if remaining <= 0:
                break
            slots = max(1, len(ordered) - index)
            fair_share = max(256, remaining // slots)
            budget = min(remaining, max(256, min(self.min_skill_chars, fair_share)))
            # If there is surplus after guaranteeing a small share to remaining
            # skills, let the current relevant skill use it without crossing the
            # aggregate ceiling.
            reserved_for_rest = max(0, slots - 1) * 256
            budget = min(remaining, max(budget, remaining - reserved_for_rest))
            excerpt = self._excerpt(skill, prompt or "", budget)
            remaining = max(0, remaining - len(excerpt))
            try:
                materialized.append(
                    replace(
                        skill,
                        selected_content=excerpt,
                        relevance_score=float(score_by_name.get(str(getattr(skill, "name", "")), 0.0)),
                    )
                )
            except TypeError:
                setattr(skill, "selected_content", excerpt)
                materialized.append(skill)
        return materialized

    def load(self, skill: Any, max_chars: int = 12000) -> str:
        selected = str(getattr(skill, "selected_content", "") or "")
        text = selected if selected else self._content(skill)
        return text[: max(256, int(max_chars))]