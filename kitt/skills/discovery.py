from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from kitt.skills.models import SkillDescriptor
from kitt.skills.skill_manager import DEFAULT_SKILLS, SkillManager


def _frontmatter(text: str) -> dict[str, str]:
    match = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    out: dict[str, str] = {}
    if match:
        for line in match.group(1).splitlines():
            if ":" not in line or line.lstrip().startswith("#"):
                continue
            key, value = line.split(":", 1)
            out[key.strip().lower()] = value.strip().strip("'\"")
    return out


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "always"}


def _int(value: str | None, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _items(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    raw = value.strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    parts = re.split(r"[,;]", raw)
    result: list[str] = []
    for part in parts:
        item = part.strip().strip("'\"")
        if item and item not in result:
            result.append(item)
    return tuple(result[:64])


def _body_hint(text: str, limit: int = 4096) -> str:
    body = re.sub(r"^---\s*\n.*?\n---\s*\n?", "", text, count=1, flags=re.S)
    headings = re.findall(r"(?m)^#{1,4}\s+(.+?)\s*$", body)
    trigger_lines = [
        line.strip()
        for line in body.splitlines()
        if any(token in line.lower() for token in ("trigger", "use when", "when to use", "applies to"))
    ]
    hint = "\n".join([*headings[:32], *trigger_lines[:24]])
    return hint[:limit]


def _descriptor(
    *,
    name: str,
    description: str,
    version: str,
    author: str,
    path: Path,
    content: str,
    source: str,
    active: bool,
    trusted: bool,
) -> SkillDescriptor:
    meta = _frontmatter(content)
    keywords = _items(meta.get("keywords") or meta.get("triggers"))
    return SkillDescriptor(
        name=name,
        description=description,
        version=version,
        author=author,
        path=path,
        source=source,
        active=active,
        trusted=trusted,
        always_apply=_bool(meta.get("alwaysapply") or meta.get("always_apply")),
        priority=max(-1000, min(1000, _int(meta.get("priority"), 0))),
        globs=_items(meta.get("globs") or meta.get("paths")),
        depends_on=_items(meta.get("depends_on") or meta.get("dependson") or meta.get("dependencies")),
        keywords=keywords,
        body_hint=_body_hint(content),
    )


def _workspace_root(roots: Iterable[Any]) -> Path | None:
    for raw in roots:
        path = Path(raw).resolve()
        if path.name == "skills" and path.parent.name == ".kitt":
            return path.parent.parent
        if (path / ".kitt").is_dir():
            return path
    return None


class SkillDiscovery:
    """Discover skills through the same trust/activation state as SkillManager.

    For a KITT workspace this is the canonical path. The generic directory scan
    remains as a compatibility fallback for callers that pass arbitrary roots.
    """

    def discover(self, roots: List[Any]) -> List[SkillDescriptor]:
        workspace = _workspace_root(roots)
        if workspace is not None:
            manager = SkillManager(str(workspace), persistence_enabled=True)
            active = set(manager.get_active_skills())
            result: list[SkillDescriptor] = []
            for skill in manager.list_skills():
                result.append(
                    _descriptor(
                        name=skill.name,
                        description=skill.description,
                        version=skill.version,
                        author=skill.author,
                        path=skill.path,
                        content=skill.skill_md_content,
                        source=skill.source,
                        active=skill.name in active,
                        trusted=skill.trusted,
                    )
                )
            return sorted(result, key=lambda item: item.name)

        found: Dict[str, SkillDescriptor] = {}
        saw_skills_root = False
        for raw_root in roots:
            root = Path(raw_root)
            if root.name == "skills":
                saw_skills_root = True
            if not root.exists():
                continue
            patterns = [
                "*/SKILL.md",
                "skills/*/SKILL.md",
                "*/skills/*/SKILL.md",
                "plugins/*/skills/*/SKILL.md",
            ]
            skill_files: list[Path] = []
            for pattern in patterns:
                skill_files.extend(root.glob(pattern))
            if not skill_files:
                skill_files = list(root.glob("**/SKILL.md"))

            for md in sorted(set(skill_files)):
                try:
                    text = md.read_text("utf-8", errors="ignore")
                    meta = _frontmatter(text)
                    name = meta.get("name", md.parent.name)
                    if name in found:
                        continue
                    found[name] = _descriptor(
                        name=name,
                        description=meta.get("description", ""),
                        version=meta.get("version", "1.0.0"),
                        author=meta.get("author", "Unknown"),
                        path=md.parent,
                        content=text,
                        source="discovered",
                        active=True,
                        trusted=True,
                    )
                except Exception:
                    continue

        if saw_skills_root:
            for name, meta in DEFAULT_SKILLS.items():
                if name in found:
                    continue
                content = str(meta.get("content", ""))
                found[name] = _descriptor(
                    name=name,
                    description=str(meta.get("description", "")),
                    version="1.0.0",
                    author="K.I.T.T. Core",
                    path=Path("."),
                    content=content,
                    source="builtin",
                    active=True,
                    trusted=True,
                )
        return sorted(found.values(), key=lambda item: item.name)

    def get_skill_completions(self, roots: List[Any]) -> List[Tuple[str, str]]:
        skills = self.discover(roots)
        completions: Dict[str, str] = {}

        for skill in skills:
            main_cmd = f"/{skill.name}"
            desc = skill.description[:60] if skill.description else "Skill"
            state = "" if skill.active else " [inactive]"
            completions[main_cmd] = f"skill: {desc}{state}"

            try:
                skill_md = skill.path / "SKILL.md"
                if skill_md.exists():
                    content = skill_md.read_text("utf-8", errors="ignore")
                    matches = re.findall(r"/(?:[a-zA-Z0-9_\-]+(?::[a-zA-Z0-9_\-]+)?)", content)
                    for match in matches:
                        cmd = match.strip()
                        if cmd.startswith("//") or "." in cmd or "/" in cmd[1:]:
                            continue
                        if cmd.lower().startswith(main_cmd.lower()) or skill.name.lower() in cmd.lower():
                            completions.setdefault(cmd, f"subskill: {skill.name}")
                    if "intensity level" in content.lower() or "mode" in content.lower():
                        for mode in ("lite", "full", "ultra", "wenyan"):
                            if mode in content.lower():
                                completions.setdefault(f"{main_cmd}:{mode}", f"skill mode: {mode}")
            except Exception:
                continue

        return sorted(completions.items(), key=lambda item: item[0])
