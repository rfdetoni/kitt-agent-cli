from __future__ import annotations

import os
import re
import stat
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from kitt.skills.models import SkillDescriptor
from kitt.skills.skill_manager import DEFAULT_SKILLS, SkillManager, SkillMetadata


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

    Canonical SkillManager instances are reused per workspace to avoid repeating
    initialization/default-skill filesystem work on every turn. Active/trust
    state and SKILL.md bodies are intentionally *not* cached: revocation and
    edits must be observed immediately at the next discovery.
    """

    _manager_lock = threading.RLock()
    _managers: Dict[str, SkillManager] = {}

    def __init__(
        self,
        *,
        max_roots: int = 8,
        max_depth: int = 5,
        max_files: int = 256,
        max_file_bytes: int = 256 * 1024,
        max_total_bytes: int = 4 * 1024 * 1024,
    ) -> None:
        self.max_roots = max(1, min(int(max_roots), 64))
        self.max_depth = max(1, min(int(max_depth), 16))
        self.max_files = max(1, min(int(max_files), 4096))
        self.max_file_bytes = max(1024, min(int(max_file_bytes), 2 * 1024 * 1024))
        self.max_total_bytes = max(
            self.max_file_bytes,
            min(int(max_total_bytes), 64 * 1024 * 1024),
        )

    def _bounded_roots(self, roots: Iterable[Any]) -> list[Path]:
        result: list[Path] = []
        seen: set[str] = set()
        for raw in roots:
            if len(result) >= self.max_roots:
                break
            try:
                path = Path(raw).expanduser().resolve(strict=False)
            except (OSError, RuntimeError, ValueError):
                continue
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            result.append(path)
        return result

    def _bounded_skill_files(self, roots: Iterable[Path]) -> list[Path]:
        found: list[Path] = []
        files_seen = 0
        bytes_seen = 0
        for root in roots:
            if len(found) >= self.max_files or files_seen >= self.max_files:
                break
            if root.is_symlink() or not root.is_dir():
                continue
            for current, dirs, files in os.walk(root, followlinks=False):
                current_path = Path(current)
                try:
                    relative = current_path.relative_to(root)
                except ValueError:
                    dirs[:] = []
                    continue
                depth = len(relative.parts)
                if depth >= self.max_depth:
                    dirs[:] = []
                else:
                    dirs[:] = [
                        name
                        for name in dirs
                        if not (current_path / name).is_symlink()
                    ]
                for name in files:
                    if files_seen >= self.max_files:
                        return found
                    path = current_path / name
                    try:
                        st = path.lstat()
                    except OSError:
                        continue
                    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
                        continue
                    files_seen += 1
                    if st.st_size > self.max_file_bytes:
                        continue
                    if bytes_seen + st.st_size > self.max_total_bytes:
                        return found
                    bytes_seen += st.st_size
                    if name == "SKILL.md":
                        found.append(path)
                        if len(found) >= self.max_files:
                            return found
        return found

    def _bounded_descriptors(
        self,
        skills: Iterable[SkillMetadata],
    ) -> list[SkillMetadata]:
        result: list[SkillMetadata] = []
        total = 0
        for skill in skills:
            if len(result) >= self.max_files:
                break
            size = len(
                str(getattr(skill, "skill_md_content", "") or "").encode(
                    "utf-8",
                    errors="replace",
                )
            )
            if size > self.max_file_bytes:
                continue
            if total + size > self.max_total_bytes:
                break
            total += size
            result.append(skill)
        return result

    @classmethod
    def _manager_for(cls, workspace: Path) -> SkillManager:
        key = str(workspace.resolve())
        with cls._manager_lock:
            manager = cls._managers.get(key)
            if manager is None:
                manager = SkillManager(key, persistence_enabled=True)
                cls._managers[key] = manager
            return manager

    def discover(self, roots: List[Any]) -> List[SkillDescriptor]:
        bounded_roots = self._bounded_roots(roots)
        workspace = _workspace_root(bounded_roots)
        if workspace is not None:
            manager = self._manager_for(workspace)
            active = set(manager.get_active_skills())
            result: list[SkillDescriptor] = []
            for skill in self._bounded_descriptors(manager.list_skills()):
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
        saw_skills_root = any(root.name == "skills" for root in bounded_roots)
        skill_files = self._bounded_skill_files(bounded_roots)
        total_loaded = 0
        for md in sorted(set(skill_files)):
            try:
                raw = md.read_bytes()
                if len(raw) > self.max_file_bytes:
                    continue
                if total_loaded + len(raw) > self.max_total_bytes:
                    break
                total_loaded += len(raw)
                text = raw.decode("utf-8", errors="ignore")
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