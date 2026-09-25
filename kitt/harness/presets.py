from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class HarnessPreset:
    id: str
    workspace_id: str
    name: str
    revision: int
    content_hash: str
    payload: dict[str, Any]
    active: bool
    created_at: float


class HarnessPresetService:
    """Revisioned declarative agent composition stored as durable project state."""

    def __init__(self, db):
        self.db = db

    @staticmethod
    def _row(row) -> HarnessPreset:
        return HarnessPreset(
            id=row["id"],
            workspace_id=row["workspace_id"],
            name=row["name"],
            revision=int(row["revision"]),
            content_hash=row["content_hash"],
            payload=json.loads(row["payload_json"] or "{}"),
            active=bool(row["is_active"]),
            created_at=float(row["created_at"]),
        )

    def ensure_revision(
        self,
        workspace_id: str,
        name: str,
        payload: dict[str, Any],
        *,
        activate: bool = False,
    ) -> HarnessPreset:
        clean_name = str(name or "").strip()
        if not clean_name:
            raise ValueError("preset name is required")
        body = dict(payload or {})
        content_hash = _digest(body)
        with self.db.get_connection() as conn:
            row = conn.execute(
                """SELECT * FROM harness_presets
                   WHERE workspace_id=? AND name=? AND content_hash=?
                   ORDER BY revision DESC LIMIT 1""",
                (workspace_id, clean_name, content_hash),
            ).fetchone()
            if row:
                preset = self._row(row)
            else:
                revision = int(
                    conn.execute(
                        """SELECT COALESCE(MAX(revision),0)+1 FROM harness_presets
                           WHERE workspace_id=? AND name=?""",
                        (workspace_id, clean_name),
                    ).fetchone()[0]
                )
                preset_id = f"hpr_{uuid.uuid4().hex}"
                now = time.time()
                conn.execute(
                    """INSERT INTO harness_presets(
                           id,workspace_id,name,revision,content_hash,payload_json,
                           is_active,created_at
                       ) VALUES(?,?,?,?,?,?,0,?)""",
                    (
                        preset_id,
                        workspace_id,
                        clean_name,
                        revision,
                        content_hash,
                        _canonical(body),
                        now,
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM harness_presets WHERE id=?",
                    (preset_id,),
                ).fetchone()
                preset = self._row(row)
        if activate:
            return self.activate(preset.id)
        return preset

    def activate(self, preset_id: str) -> HarnessPreset:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM harness_presets WHERE id=?",
                (preset_id,),
            ).fetchone()
            if not row:
                raise ValueError("preset not found")
            workspace_id = row["workspace_id"]
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE harness_presets SET is_active=0 WHERE workspace_id=?",
                (workspace_id,),
            )
            conn.execute(
                "UPDATE harness_presets SET is_active=1 WHERE id=?",
                (preset_id,),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM harness_presets WHERE id=?",
                (preset_id,),
            ).fetchone()
        return self._row(row)

    def active(self, workspace_id: str) -> HarnessPreset | None:
        with self.db.get_connection() as conn:
            row = conn.execute(
                """SELECT * FROM harness_presets
                   WHERE workspace_id=? AND is_active=1
                   ORDER BY created_at DESC LIMIT 1""",
                (workspace_id,),
            ).fetchone()
        return self._row(row) if row else None

    def revisions(
        self,
        workspace_id: str,
        name: str,
        *,
        limit: int = 50,
    ) -> list[HarnessPreset]:
        with self.db.get_connection() as conn:
            rows = conn.execute(
                """SELECT * FROM harness_presets
                   WHERE workspace_id=? AND name=?
                   ORDER BY revision DESC LIMIT ?""",
                (workspace_id, str(name), max(1, min(int(limit), 500))),
            ).fetchall()
        return [self._row(row) for row in rows]
