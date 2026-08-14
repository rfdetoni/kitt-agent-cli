"""Conversation working set: compact references, not transcript dumps."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Iterable, List


@dataclass
class WorkingSetItem:
    path: str
    weight: float
    last_turn_id: str
    last_touched: float
    kind: str = "file"
    content_hash: str = ""


class ConversationWorkingSetStore:
    """Small JSON store keyed by conversation id.

    ponytail: JSON file, no SQLite migration; move to DB when multi-process
    writes matter.
    """

    def __init__(self, root_dir: str | Path, persistence_enabled: bool = True, max_items: int = 40):
        self.root_path = Path(root_dir).resolve()
        self.persistence_enabled = persistence_enabled
        self.max_items = max_items
        self._memory: Dict[str, Dict[str, WorkingSetItem]] = {}
        self.path = self.root_path / ".kitt" / "working_set.json"

    def _load_all(self) -> Dict[str, Dict[str, WorkingSetItem]]:
        if not self.persistence_enabled or not self.path.exists():
            return self._memory
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            loaded = {
                conv: {path: WorkingSetItem(**item) for path, item in items.items()}
                for conv, items in raw.items()
            }
            self._memory = loaded
            return self._memory
        except Exception:
            return self._memory

    def _save_all(self, data: Dict[str, Dict[str, WorkingSetItem]]) -> None:
        self._memory = data
        if not self.persistence_enabled:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {conv: {path: asdict(item) for path, item in items.items()} for conv, items in data.items()}
        tmp_path = self.path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
        tmp_path.replace(self.path)

    def touch_paths(
        self,
        conversation_id: str,
        paths: Iterable[str],
        turn_id: str,
        *,
        weight: float = 1.0,
        kind: str = "file",
        content_hash: str = "",
    ) -> None:
        clean_paths = [path for path in paths if path and not path.startswith("/")]
        if not clean_paths:
            return
        data = self._load_all()
        items = data.setdefault(conversation_id, {})
        now = time.time()
        for path in clean_paths:
            old = items.get(path)
            items[path] = WorkingSetItem(
                path=path,
                weight=min(5.0, (old.weight * 0.85 if old else 0.0) + weight),
                last_turn_id=turn_id,
                last_touched=now,
                kind=kind,
                content_hash=content_hash or (old.content_hash if old else ""),
            )
        ranked = sorted(items.values(), key=lambda item: (item.weight, item.last_touched), reverse=True)[: self.max_items]
        data[conversation_id] = {item.path: item for item in ranked}
        self._save_all(data)

    @staticmethod
    def _decayed_score(item: WorkingSetItem, now: float) -> float:
        # Ponytail: simple hour-based decay score
        elapsed_hours = max(0.0, (now - item.last_touched) / 3600.0)
        return item.weight / (1.0 + 0.1 * elapsed_hours)

    def paths(self, conversation_id: str, limit: int = 12) -> List[str]:
        items = self._load_all().get(conversation_id, {})
        now = time.time()
        ranked = sorted(items.values(), key=lambda item: (self._decayed_score(item, now), item.last_touched), reverse=True)
        return [item.path for item in ranked[:limit]]

    def context(self, conversation_id: str, max_items: int = 12) -> str:
        items = self._load_all().get(conversation_id, {})
        now = time.time()
        ranked = sorted(items.values(), key=lambda item: (self._decayed_score(item, now), item.last_touched), reverse=True)[:max_items]
        return "\n".join(f"- {item.path} ({item.kind}, weight={item.weight:.2f})" for item in ranked)
