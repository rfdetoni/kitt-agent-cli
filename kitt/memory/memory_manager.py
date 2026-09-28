"""Persistent Agent memory facade with deterministic local authority and shared mirroring."""
from __future__ import annotations

import os
import re
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, List, Literal, Optional

from kitt.context_filter.prompt_budget import TokenCounter
from kitt.memory.shared_client import SharedMemoryClient, SharedMemoryUnavailable


@dataclass
class MemoryItem:
    text: str
    scope: Literal["GLOBAL", "PROJECT"]
    priority: int = 1
    tags: List[str] = field(default_factory=list)
    created_at: str = ""


class MemoryManager:
    """Keep Agent writes locally authoritative and mirror them to shared kitt-memory.

    The Agent-owned structured repository is the durable source for Agent-created
    project memory. Shared kittd memory is an interoperability mirror/source and
    is merged into recall when available; daemon availability can therefore add
    shared knowledge but can never hide locally durable Agent memory. Markdown is
    a last-resort recovery backend only when no structured repository is usable.
    """

    def __init__(
        self,
        root_dir: str = ".",
        persistence_enabled: bool = True,
        memory_repo: Optional[Any] = None,
        workspace_id: Optional[str] = None,
        shared_client: Optional[Any] = None,
    ):
        self.root_dir = Path(root_dir).resolve()
        self.persistence_enabled = persistence_enabled
        self.memory_repo = memory_repo
        self.workspace_id = workspace_id or "default"
        self.shared_client = shared_client
        self.project_mem_path = self.root_dir / ".kitt" / "memory" / "project_memory.md"
        self.global_mem_path = Path.home() / ".kitt" / "global_memory.md"

        if persistence_enabled:
            self._ensure_files()

    def _ensure_files(self) -> None:
        self.project_mem_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.project_mem_path.exists():
            self.project_mem_path.write_text(
                "# Project Memory & Guidelines\n\n",
                encoding="utf-8",
            )
        self.global_mem_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.global_mem_path.exists():
            self.global_mem_path.write_text(
                "# K.I.T.T. Global User Preferences\n\n",
                encoding="utf-8",
            )

    def _shared(self) -> Any:
        if self.shared_client is None:
            self.shared_client = SharedMemoryClient()
        return self.shared_client

    @contextmanager
    def _project_file_lock(self) -> Iterator[None]:
        lock_path = self.project_mem_path.with_name(self.project_mem_path.name + ".lock")
        deadline = time.monotonic() + 5.0
        fd: int | None = None
        while fd is None:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.write(fd, str(os.getpid()).encode("ascii", errors="ignore"))
            except FileExistsError:
                try:
                    stale = time.time() - lock_path.stat().st_mtime > 30.0
                except OSError:
                    stale = False
                if stale:
                    try:
                        lock_path.unlink()
                    except OSError:
                        pass
                    continue
                if time.monotonic() >= deadline:
                    raise TimeoutError("Timed out acquiring project memory lock") from None
                time.sleep(0.025)
        try:
            yield
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                lock_path.unlink()
            except OSError:
                pass

    def add_project_memory(
        self,
        note: str,
        kind: str = "PROJECT_RULE",
        pinned: bool = True,
    ) -> None:
        if not self.persistence_enabled:
            return
        note = note.strip()
        if not note:
            return

        # Agent structured memory is canonical for Agent-originated writes.
        # Mirroring is best effort and never changes whether the local write
        # succeeded or whether the memory remains visible during daemon outages.
        if self.memory_repo is not None:
            try:
                self.memory_repo.add_direct_memory(
                    self.workspace_id,
                    note,
                    kind=kind,
                    pinned=pinned,
                )
                try:
                    self._shared().remember(
                        self.workspace_id,
                        note,
                        kind=kind,
                        pinned=pinned,
                    )
                except SharedMemoryUnavailable:
                    pass
                return
            except Exception:
                # A broken local repository must not silently become an
                # alternate authority if shared memory is reachable.
                pass

        try:
            self._shared().remember(self.workspace_id, note, kind=kind, pinned=pinned)
            return
        except SharedMemoryUnavailable:
            self._append_markdown(note)

    def _append_markdown(self, note: str) -> None:
        with self._project_file_lock():
            content = (
                self.project_mem_path.read_text(encoding="utf-8", errors="ignore")
                if self.project_mem_path.exists()
                else "# Project Memory & Guidelines\n\n"
            )
            existing = {
                line.strip()[2:].strip()
                for line in content.splitlines()
                if line.strip().startswith("- ")
            }
            if note in existing:
                return
            next_content = content.rstrip() + f"\n- {note}\n"
            temporary = self.project_mem_path.with_name(
                "." + self.project_mem_path.name + "." + uuid.uuid4().hex[:10] + ".tmp"
            )
            try:
                temporary.write_text(next_content, encoding="utf-8")
                os.replace(temporary, self.project_mem_path)
            finally:
                try:
                    temporary.unlink()
                except OSError:
                    pass

    @staticmethod
    def _markdown_items(path: Path, scope: Literal["GLOBAL", "PROJECT"], priority: int) -> List[MemoryItem]:
        if not path.exists():
            return []
        items: list[MemoryItem] = []
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip()
            if stripped.startswith("- "):
                text = stripped[2:].strip()
                if text:
                    items.append(MemoryItem(text, scope, priority))
        return items

    def clear_project_memory(self) -> None:
        if not self.persistence_enabled:
            return

        local_texts = [
            item.text
            for item in self._markdown_items(self.project_mem_path, "PROJECT", 1)
        ]
        archived = []
        if self.memory_repo is not None:
            try:
                archived = list(
                    self.memory_repo.archive_active_memories(self.workspace_id)
                )
                local_texts.extend(
                    str(record.content).strip()
                    for record in archived
                    if str(record.content).strip()
                )
            except Exception:
                archived = []

        with self._project_file_lock():
            temporary = self.project_mem_path.with_name(
                "." + self.project_mem_path.name + "." + uuid.uuid4().hex[:10] + ".tmp"
            )
            try:
                temporary.write_text("# Project Memory & Guidelines\n\n", encoding="utf-8")
                os.replace(temporary, self.project_mem_path)
            finally:
                try:
                    temporary.unlink()
                except OSError:
                    pass

        # Remove exact mirrored Agent workspace records. Shared cleanup is best
        # effort; local archival is already authoritative for Agent recall.
        seen_ids: set[str] = set()
        for text in dict.fromkeys(local_texts):
            try:
                records = self._shared().recall(self.workspace_id, text, limit=12)
            except SharedMemoryUnavailable:
                break
            for record in records:
                record_id = str(record.get("id", "")).strip()
                if (
                    record_id
                    and record_id not in seen_ids
                    and str(record.get("content", "")).strip() == text
                    and str(record.get("workspace_id", "")) == self.workspace_id
                    and str(record.get("scope", "")).lower() == "workspace"
                ):
                    try:
                        self._shared().forget(record_id)
                        seen_ids.add(record_id)
                    except SharedMemoryUnavailable:
                        return

    def get_items(self) -> List[MemoryItem]:
        items: List[MemoryItem] = []
        seen_texts: set[str] = set()

        for item in self._markdown_items(self.global_mem_path, "GLOBAL", 2):
            if item.text not in seen_texts:
                seen_texts.add(item.text)
                items.append(item)

        repo_available = False
        if self.memory_repo is not None:
            try:
                records = self.memory_repo.get_active_memories(self.workspace_id)
                repo_available = True
                for rec in records:
                    clean = rec.content.strip()
                    if clean and clean not in seen_texts:
                        seen_texts.add(clean)
                        priority = (
                            3
                            if rec.pinned
                            else 2
                            if rec.kind in ("PROJECT_RULE", "ARCHITECTURE_DECISION")
                            else 1
                        )
                        items.append(MemoryItem(clean, "PROJECT", priority))
            except Exception:
                repo_available = False

        if not repo_available:
            for item in self._markdown_items(self.project_mem_path, "PROJECT", 1):
                if item.text not in seen_texts:
                    seen_texts.add(item.text)
                    items.append(item)
        return items

    def get_relevant_memories(self, prompt: str) -> List[MemoryItem]:
        combined = {item.text: item for item in self.get_items()}
        if prompt:
            try:
                records = self._shared().recall(self.workspace_id, prompt, limit=8)
                for record in records:
                    text = str(record.get("content", "")).strip()
                    if not text:
                        continue
                    scope = (
                        "GLOBAL"
                        if str(record.get("scope", "")).lower() == "global"
                        else "PROJECT"
                    )
                    candidate = MemoryItem(
                        text=text,
                        scope=scope,
                        priority=3 if record.get("pinned") else 2,
                    )
                    current = combined.get(text)
                    if current is None or candidate.priority > current.priority:
                        combined[text] = candidate
            except SharedMemoryUnavailable:
                pass
        return self._rank(prompt, list(combined.values()))

    @staticmethod
    def _rank(prompt: str, items: List[MemoryItem]) -> List[MemoryItem]:
        words = set(re.findall(r"[a-zA-Z0-9_+-]{4,}", prompt.lower()))
        if not words:
            return sorted(items, key=lambda item: item.priority, reverse=True)[:5]

        relevant: list[tuple[int, MemoryItem]] = []
        for item in items:
            item_words = set(re.findall(r"[a-zA-Z0-9_+-]{4,}", item.text.lower()))
            overlap = words.intersection(item_words)
            score = len(overlap) + item.priority
            if overlap or item.priority >= 2:
                relevant.append((score, item))
        relevant.sort(key=lambda row: row[0], reverse=True)
        return [item for _, item in relevant[:8]]

    def get_memory_context(self, prompt: str = "", max_tokens: int = 400) -> str:
        items = self.get_relevant_memories(prompt) if prompt else self.get_items()

        if not prompt:
            # Keep the human /memory surface stable without manufacturing
            # preference content. Empty sections are descriptive UI, not memory.
            grouped = {
                "GLOBAL": [item.text for item in items if item.scope == "GLOBAL"],
                "PROJECT": [item.text for item in items if item.scope == "PROJECT"],
            }
            sections = []
            for scope, title in (("GLOBAL", "Global Memory"), ("PROJECT", "Project Memory")):
                values = grouped[scope]
                body = "\n".join(f"- {value}" for value in values) if values else "(empty)"
                sections.append(f"--- {title} ---\n{body}")
            return "\n\n".join(sections)

        lines: list[str] = []
        used = 0
        for item in items:
            line = f"- [{item.scope}] {item.text}"
            tokens = TokenCounter.count_tokens(line)
            if used + tokens > max_tokens:
                continue
            lines.append(line)
            used += tokens
        return "\n".join(lines)
