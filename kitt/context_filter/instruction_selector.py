from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from kitt.security.workspace_fs import WorkspaceFileSystem

_WORD_RE = re.compile(r"[a-z0-9_+./#-]{2,}", re.I)
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.S)


@dataclass(frozen=True)
class InstructionDescriptor:
    name: str
    path: str
    content: str
    kind: str
    mandatory: bool = False
    always_apply: bool = False
    priority: int = 0
    globs: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()


def _frontmatter(text: str) -> dict[str, str]:
    match = _FRONTMATTER_RE.match(text or "")
    if not match:
        return {}
    result: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split(":", 1)
        result[key.strip().lower()] = value.strip().strip("'\"")
    return result


def _items(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    raw = value.strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    out: list[str] = []
    for item in re.split(r"[,;]", raw):
        clean = item.strip().strip("'\"")
        if clean and clean not in out:
            out.append(clean)
    return tuple(out[:64])


def _bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "always", "always_on"}


def _int(value: str | None, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


class LazyInstructionSelector:
    """Select AGENTS/rule/spec instructions under one deterministic body budget."""

    OPTIONAL_ROOTS = (".agents/rules", ".agents/specs", ".kitt/rules", ".kitt/specs")

    def __init__(
        self,
        root_dir: str | Path,
        *,
        workspace_fs: WorkspaceFileSystem | None = None,
        max_total_chars: int = 16000,
        max_optional_files: int = 128,
    ):
        self.root = Path(root_dir).resolve()
        self.fs = workspace_fs or WorkspaceFileSystem(self.root)
        self.max_total_chars = max(2048, min(int(max_total_chars), 128000))
        self.max_optional_files = max(1, min(int(max_optional_files), 512))

    @staticmethod
    def _terms(value: str) -> set[str]:
        return {term.lower() for term in _WORD_RE.findall(value or "")}

    def descriptor(self, path: str, content: str, *, kind: str, mandatory: bool = False) -> InstructionDescriptor:
        meta = _frontmatter(content)
        name = meta.get("name") or Path(path).stem
        trigger = meta.get("trigger")
        priority = max(-1000, min(1000, _int(meta.get("priority"), 0)))
        return InstructionDescriptor(
            name=name,
            path=path,
            content=content,
            kind=kind,
            mandatory=mandatory,
            always_apply=mandatory or _bool(meta.get("alwaysapply") or meta.get("always_apply") or trigger),
            priority=priority,
            globs=_items(meta.get("globs") or meta.get("paths")),
            depends_on=_items(meta.get("depends_on") or meta.get("dependson") or meta.get("dependencies")),
            keywords=_items(meta.get("keywords") or meta.get("triggers")),
        )

    def discover_optional(self) -> list[InstructionDescriptor]:
        found: list[InstructionDescriptor] = []
        for rel_root in self.OPTIONAL_ROOTS:
            if len(found) >= self.max_optional_files or not self.fs.is_safe_directory(rel_root):
                continue
            root = self.root / rel_root
            try:
                candidates = sorted(root.rglob("*.md"))
            except OSError:
                continue
            for path in candidates:
                if len(found) >= self.max_optional_files:
                    break
                try:
                    rel = path.relative_to(self.root).as_posix()
                    if not self.fs.exists_regular(rel):
                        continue
                    text, _ = self.fs.read_text(rel, max_bytes=256 * 1024)
                except (OSError, ValueError, PermissionError, FileNotFoundError):
                    continue
                kind = "spec" if "/spec" in rel.lower() else "rule"
                found.append(self.descriptor(rel, text, kind=kind))
        return found

    def _score(self, item: InstructionDescriptor, query: str, target_path: str) -> float:
        if item.mandatory:
            return 10000.0 + item.priority
        score = 1000.0 if item.always_apply else 0.0
        score += item.priority / 10.0
        target = target_path.replace("\\", "/")
        if item.globs and target and any(fnmatch.fnmatch(target, glob) for glob in item.globs):
            score += 200.0
        query_terms = self._terms(query)
        indexed = self._terms(" ".join((item.name, item.path, " ".join(item.keywords), item.content[:4096])))
        overlap = query_terms.intersection(indexed)
        score += float(len(overlap) * 3)
        keyword_terms = {word.lower() for word in item.keywords}
        score += float(len(query_terms.intersection(keyword_terms)) * 12)
        if item.name.lower() in query.lower():
            score += 50.0
        return score

    @staticmethod
    def _sections(content: str) -> list[tuple[str, str]]:
        body = _FRONTMATTER_RE.sub("", content, count=1).strip()
        if not body:
            return []
        sections: list[tuple[str, str]] = []
        title = "Overview"
        current: list[str] = []
        for line in body.splitlines():
            if re.match(r"^#{1,4}\s+", line):
                if current:
                    sections.append((title, "\n".join(current).strip()))
                title = re.sub(r"^#{1,4}\s+", "", line).strip()
                current = [line]
            else:
                current.append(line)
        if current:
            sections.append((title, "\n".join(current).strip()))
        return [(title, text) for title, text in sections if text]

    def _excerpt(self, item: InstructionDescriptor, query: str, budget: int) -> str:
        if len(item.content) <= budget:
            return item.content
        query_terms = self._terms(query)
        scored: list[tuple[int, int, str]] = []
        for index, (title, text) in enumerate(self._sections(item.content)):
            terms = self._terms(f"{title} {text[:4096]}")
            safety = 100 if any(marker in title.lower() for marker in (
                "security", "safety", "mandatory", "constraint", "rule", "permission", "approval"
            )) else 0
            scored.append((safety + len(query_terms.intersection(terms)) * 5 + (2 if index == 0 else 0), index, text))
        scored.sort(key=lambda row: (-row[0], row[1]))
        chosen: list[tuple[int, str]] = []
        used = 0
        for _, index, text in scored:
            remaining = budget - used
            if remaining < 96:
                break
            piece = text[:remaining]
            chosen.append((index, piece))
            used += len(piece) + 2
        chosen.sort(key=lambda row: row[0])
        return "\n\n".join(text for _, text in chosen)[:budget]

    def select(
        self,
        mandatory: Iterable[InstructionDescriptor],
        *,
        query: str = "",
        target_path: str = "",
        max_optional: int = 8,
    ) -> list[InstructionDescriptor]:
        mandatory_items = list(mandatory)
        optional = self.discover_optional()
        scored = [
            (self._score(item, query, target_path), item.priority, item.name, item)
            for item in optional
        ]
        scored = [row for row in scored if row[0] > 0]
        scored.sort(key=lambda row: (-row[0], -row[1], row[2], row[3].path))
        by_name = {item.name: item for item in optional}
        ordered_optional: list[InstructionDescriptor] = []
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(item: InstructionDescriptor) -> None:
            if item.name in visited or len(ordered_optional) >= max_optional:
                return
            if item.name in visiting:
                return
            visiting.add(item.name)
            for dep_name in item.depends_on:
                dep = by_name.get(dep_name)
                if dep is not None:
                    visit(dep)
            visiting.discard(item.name)
            visited.add(item.name)
            if len(ordered_optional) < max_optional:
                ordered_optional.append(item)

        for _, _, _, item in scored:
            visit(item)

        selected = [*mandatory_items, *ordered_optional]
        if not selected:
            return []
        remaining = self.max_total_chars
        materialized: list[InstructionDescriptor] = []
        for index, item in enumerate(selected):
            slots = max(1, len(selected) - index)
            budget = max(256, remaining // slots)
            excerpt = self._excerpt(item, query, budget)
            remaining = max(0, remaining - len(excerpt))
            materialized.append(
                InstructionDescriptor(
                    **{**item.__dict__, "content": excerpt}
                )
            )
            if remaining <= 0:
                break
        return materialized
