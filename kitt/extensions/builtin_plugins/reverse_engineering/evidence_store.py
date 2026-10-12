"""Operator-owned SQLite evidence database; MCP only exposes bounded read access.

Import runs as an explicit offline CLI command, never as a model-callable MCP tool.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import quote

from .evidence import EvidenceError
from .probe_evidence import _verify

MAX_RECEIPT = 200 * 1024
NAME = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY,
    probe TEXT NOT NULL,
    platform TEXT NOT NULL,
    receipt_sha256 TEXT NOT NULL UNIQUE,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS observations_probe ON observations(probe, id);
"""


def _database(root: Path) -> Path:
    root = root.resolve(strict=True)
    path = root / ".kitt" / "reverse-engineering" / "observations.sqlite3"
    for item in (root / ".kitt", path.parent, path):
        if item.is_symlink():
            raise EvidenceError("Symlink evidence paths are forbidden")
    return path


def import_receipt(root: Path, receipt: dict) -> bool:
    """Idempotently store only validated, successful JSON observations."""
    # The receipt hash establishes consistency, not guest identity/authenticity.
    obs = _verify(receipt)
    probe = receipt["probe"]
    if not NAME.fullmatch(probe):
        raise EvidenceError("Invalid probe identifier")
    if len(json.dumps(receipt, ensure_ascii=False).encode()) > MAX_RECEIPT:
        raise EvidenceError("Receipt too large")
    db = _database(root)
    db.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
        os.chmod(db.parent, 0o700)
    payload = json.dumps(obs, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    with sqlite3.connect(db, timeout=5) as conn:
        conn.executescript(SCHEMA)
        cur = conn.execute(
            "INSERT OR IGNORE INTO observations(probe, platform, receipt_sha256, payload) VALUES (?, ?, ?, ?)",
            (probe, receipt["platform"], receipt["sha256"], payload),
        )
        inserted = cur.rowcount == 1
    if os.name == "posix":
        os.chmod(db, 0o600)
    return inserted


def query(root: Path, probe: str, limit: int = 10) -> list[dict]:
    if not isinstance(probe, str) or not NAME.fullmatch(probe):
        raise EvidenceError("Invalid probe identifier")
    if type(limit) is not int or limit < 1 or limit > 25:
        raise EvidenceError("Limit must be an integer between 1 and 25")
    db = _database(root)
    if not db.exists():
        return []
    # URI read-only opening; no database creation or mutation from the MCP surface.
    uri = db.resolve(strict=True).as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=3) as conn:
        conn.execute("PRAGMA query_only=ON")
        rows = conn.execute(
            "SELECT probe,platform,receipt_sha256,payload,created_at "
            "FROM observations WHERE probe=? ORDER BY id DESC LIMIT ?",
            (probe, limit),
        ).fetchall()
    return [{"probe": p, "platform": platform, "receipt_sha256": sha,
             "observation": json.loads(payload), "created_at": created}
            for p, platform, sha, payload, created in rows]


def main() -> None:
    parser = argparse.ArgumentParser(description="Operator-only import of guest probe receipts")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    if args.receipt.stat().st_size > MAX_RECEIPT:
        parser.error("Receipt exceeds limit")
    receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
    try:
        inserted = import_receipt(args.workspace, receipt)
    except (EvidenceError, OSError, sqlite3.Error) as ex:
        parser.error(str(ex))
    print(json.dumps({"inserted": inserted, "probe": receipt["probe"]}))


if __name__ == "__main__":
    main()
