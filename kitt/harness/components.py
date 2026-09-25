from __future__ import annotations

import hashlib
import json
import time
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


class HarnessComponentSnapshotService:
    """Privacy-safe logical component identities and revision digests."""

    def __init__(self, db):
        self.db = db

    @staticmethod
    def normalize(components: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        seen = set()
        for raw in components:
            kind = str(raw.get("kind") or "").strip()
            logical_id = str(raw.get("id") or raw.get("name") or "").strip()
            if not kind or not logical_id:
                continue
            source = str(raw.get("source") or "").strip()
            revision = str(
                raw.get("revision")
                or raw.get("digest")
                or raw.get("version")
                or ""
            ).strip()
            state = str(raw.get("state") or "").strip()
            key = (kind, logical_id, source)
            if key in seen:
                continue
            seen.add(key)
            result.append(
                {
                    "kind": kind,
                    "id": logical_id,
                    "source": source,
                    "revision": revision,
                    "state": state,
                }
            )
        result.sort(key=lambda item: (item["kind"], item["id"], item["source"]))
        return result

    def capture(
        self,
        workspace_id: str,
        components: list[dict[str, Any]],
    ) -> dict[str, Any]:
        normalized = self.normalize(components)
        digest = _digest(normalized)
        snapshot_id = f"hcs_{digest[:32]}"
        now = time.time()
        with self.db.get_connection() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO harness_component_snapshots(
                       id,workspace_id,snapshot_hash,components_json,created_at
                   ) VALUES(?,?,?,?,?)""",
                (
                    snapshot_id,
                    workspace_id,
                    digest,
                    _canonical(normalized),
                    now,
                ),
            )
        return {
            "id": snapshot_id,
            "snapshot_hash": digest,
            "components": normalized,
            "created_at": now,
        }

    def get(self, snapshot_id: str) -> dict[str, Any] | None:
        with self.db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM harness_component_snapshots WHERE id=?",
                (snapshot_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "workspace_id": row["workspace_id"],
            "snapshot_hash": row["snapshot_hash"],
            "components": json.loads(row["components_json"] or "[]"),
            "created_at": float(row["created_at"]),
        }

    def diff(self, before_id: str, after_id: str) -> dict[str, Any]:
        before = self.get(before_id)
        after = self.get(after_id)
        if before is None or after is None:
            raise ValueError("component snapshot not found")

        def keyed(snapshot):
            return {
                (item["kind"], item["id"], item.get("source", "")): item
                for item in snapshot["components"]
            }

        left = keyed(before)
        right = keyed(after)
        added = [right[key] for key in sorted(right.keys() - left.keys())]
        removed = [left[key] for key in sorted(left.keys() - right.keys())]
        changed = []
        for key in sorted(left.keys() & right.keys()):
            if left[key] != right[key]:
                changed.append({"before": left[key], "after": right[key]})
        return {
            "before": before_id,
            "after": after_id,
            "added": added,
            "removed": removed,
            "changed": changed,
            "has_drift": bool(added or removed or changed),
        }
