"""Operator-facing session and incident diagnostics."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from kitt.history.database import HistoryDatabase
from kitt.history.repository import HistoryRepository, resolve_workspace_identity


_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_DURATION = re.compile(r"^(\d+)([smhd])$", re.IGNORECASE)
_INCIDENT_TERMS = (
    "error",
    "fail",
    "timeout",
    "timed_out",
    "retry",
    "recovery",
    "restart",
    "crash",
    "blocked",
    "cancel",
    "disconnect",
    "truncated",
)


def _clean(value: Any, limit: int = 160) -> str:
    text = _CONTROL_CHARS.sub(" ", str(value or ""))
    text = " ".join(text.split())
    return text[:limit]


def _parse_time(value: Any) -> Optional[datetime]:
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _since_time(value: str, now: Optional[datetime] = None) -> datetime:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    raw = str(value or "1h").strip()
    match = _DURATION.fullmatch(raw)
    if match:
        amount = int(match.group(1))
        unit = match.group(2).lower()
        seconds = amount * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
        return current - timedelta(seconds=seconds)
    parsed = _parse_time(raw)
    if parsed is None:
        raise ValueError(
            "--since must be an ISO-8601 timestamp or a duration such as 30m, 2h or 1d"
        )
    return parsed


def _human_age(seconds: Optional[float]) -> str:
    if seconds is None:
        return "-"
    value = max(0, int(seconds))
    if value < 60:
        return f"{value}s"
    if value < 3600:
        return f"{value // 60}m"
    if value < 86400:
        return f"{value // 3600}h"
    return f"{value // 86400}d"


def build_session_report(
    root_dir: str,
    sessions: Optional[Iterable[dict[str, Any]]] = None,
    *,
    active_id: str = "",
    limit: int = 20,
    now: Optional[float] = None,
) -> list[dict[str, Any]]:
    """Enrich daemon/local session rows from the Agent's durable SQLite state."""
    cap = min(100, max(1, int(limit)))
    db = HistoryDatabase(root_dir)
    try:
        if sessions is None:
            identity = resolve_workspace_identity(db, root_dir)
            repo = HistoryRepository(db)
            source_rows = repo.list_conversations(identity.id, limit=cap)
        else:
            source_rows = list(sessions)[:cap]

        current = float(
            now if now is not None else datetime.now(timezone.utc).timestamp()
        )
        rows: list[dict[str, Any]] = []
        with db.get_connection() as conn:
            for raw in source_rows:
                sid = str(raw.get("id") or "")
                if not sid:
                    continue
                conversation = conn.execute(
                    """SELECT status, updated_at, last_turn_at
                       FROM conversations WHERE id=?""",
                    (sid,),
                ).fetchone()
                latest = conn.execute(
                    """SELECT state, mode, semantic_intent, started_at,
                              completed_at, error_code
                       FROM turns WHERE conversation_id=?
                       ORDER BY ordinal DESC LIMIT 1""",
                    (sid,),
                ).fetchone()
                usage = conn.execute(
                    """SELECT COALESCE(SUM(input_tokens),0),
                              COALESCE(SUM(output_tokens),0)
                       FROM telemetry_events WHERE conversation_id=?""",
                    (sid,),
                ).fetchone()

                last_activity = None
                if latest:
                    last_activity = latest["completed_at"] or latest["started_at"]
                if last_activity is None and conversation:
                    last_activity = (
                        conversation["last_turn_at"]
                        or conversation["updated_at"]
                    )
                age_seconds = (
                    max(0.0, current - float(last_activity))
                    if last_activity is not None
                    else None
                )
                status = raw.get("status")
                if not status and conversation:
                    status = conversation["status"]

                rows.append(
                    {
                        "id": sid,
                        "title": _clean(raw.get("title") or sid, 120),
                        "status": _clean(status or "UNKNOWN", 32),
                        "active": sid == active_id,
                        "last_state": _clean(
                            latest["state"] if latest else "",
                            32,
                        ),
                        "activity": _clean(
                            (latest["semantic_intent"] or latest["mode"])
                            if latest
                            else "",
                            48,
                        ),
                        "last_error": _clean(
                            latest["error_code"] if latest else "",
                            200,
                        ),
                        "age_seconds": age_seconds,
                        "age": _human_age(age_seconds),
                        "input_tokens": int(usage[0] if usage else 0),
                        "output_tokens": int(usage[1] if usage else 0),
                    }
                )
        return rows
    finally:
        db.close()


def render_session_report(
    rows: list[dict[str, Any]],
    *,
    json_output: bool = False,
) -> str:
    if json_output:
        return json.dumps({"sessions": rows}, indent=2, ensure_ascii=False)
    if not rows:
        return "No KITT sessions found."

    header = (
        f"{'ID':12}  {'STATUS':12}  {'TURN':12}  "
        f"{'AGE':>6}  {'TOKENS':>9}  TITLE"
    )
    lines = ["=== KITT Sessions ===", header, "-" * len(header)]
    for row in rows:
        marker = "*" if row.get("active") else " "
        tokens = int(row.get("input_tokens", 0)) + int(row.get("output_tokens", 0))
        lines.append(
            f"{marker}{str(row.get('id', ''))[:11]:11}  "
            f"{str(row.get('status', ''))[:12]:12}  "
            f"{str(row.get('last_state', ''))[:12]:12}  "
            f"{str(row.get('age', '-')):>6}  "
            f"{tokens:>9}  {row.get('title', '')}"
        )
        detail = row.get("last_error")
        if detail:
            lines.append(f"  error: {detail}")
    return "\n".join(lines)


def _candidate_logs(root_dir: str) -> list[Path]:
    root = Path(root_dir).expanduser().resolve(strict=False)
    candidates: dict[str, Path] = {}
    configured = str(
        os.getenv("KITT_LOG_FILE", "")
        or os.getenv("KITT_DEBUG_LOG", "")
    ).strip()
    if configured:
        path = Path(configured).expanduser().resolve(strict=False)
        if path.is_file():
            candidates[str(path)] = path
    for folder in (root / ".kitt" / "logs", root / ".kitt" / "daemon"):
        if not folder.is_dir():
            continue
        for path in folder.rglob("*.log*"):
            if path.is_file():
                candidates[str(path.resolve(strict=False))] = path
    return sorted(
        candidates.values(),
        key=lambda path: path.stat().st_mtime if path.exists() else 0.0,
        reverse=True,
    )[:32]


def _tail_json_lines(path: Path, max_bytes: int = 4 * 1024 * 1024):
    try:
        with path.open("rb") as handle:
            size = path.stat().st_size
            start = max(0, size - max_bytes)
            handle.seek(start)
            if start:
                handle.readline()
            for raw in handle:
                try:
                    line = raw.decode("utf-8", "replace").strip()
                    if line:
                        yield json.loads(line)
                except (json.JSONDecodeError, UnicodeError):
                    continue
    except (OSError, ValueError):
        return


def collect_incidents(
    root_dir: str,
    *,
    since: str = "1h",
    session: Optional[str] = None,
    limit: int = 100,
    now: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    cutoff = _since_time(since, now=now)
    session_filter = str(session or "").strip().casefold()
    cap = min(500, max(1, int(limit)))
    incidents: list[dict[str, Any]] = []

    for path in _candidate_logs(root_dir):
        for record in _tail_json_lines(path):
            if not isinstance(record, dict):
                continue
            timestamp = _parse_time(record.get("ts"))
            if timestamp is None or timestamp < cutoff:
                continue
            level = str(record.get("level") or "INFO").upper()
            extra = record.get("extra_data")
            extra = extra if isinstance(extra, dict) else {}
            event = str(extra.get("event") or record.get("msg") or "")
            haystack = json.dumps(
                {
                    "event": event,
                    "module": record.get("module"),
                    "extra": extra,
                },
                ensure_ascii=False,
                default=str,
            ).casefold()
            if session_filter and session_filter not in haystack:
                continue
            interesting = (
                level in {"WARNING", "WARN", "ERROR", "CRITICAL"}
                or any(term in haystack for term in _INCIDENT_TERMS)
            )
            if not interesting:
                continue

            error = extra.get("error")
            if isinstance(error, dict):
                error = error.get("message") or error.get("type")
            detail = _clean(error or record.get("msg") or event, 240)
            subject = ""
            for key in (
                "session_id",
                "conversation_id",
                "turn_id",
                "child_id",
                "model",
            ):
                if extra.get(key):
                    subject = _clean(extra.get(key), 80)
                    break
            incidents.append(
                {
                    "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                    "level": level,
                    "event": _clean(event, 120),
                    "subject": subject,
                    "detail": detail,
                    "source": path.name,
                }
            )

    incidents.sort(key=lambda item: item["timestamp"])
    return incidents[-cap:]


def render_incident_report(
    incidents: list[dict[str, Any]],
    *,
    json_output: bool = False,
) -> str:
    if json_output:
        return json.dumps(
            {"incidents": incidents},
            indent=2,
            ensure_ascii=False,
        )
    if not incidents:
        return "No noteworthy KITT events found in the selected window."
    lines = ["=== KITT Incident Timeline ==="]
    for item in incidents:
        subject = f" [{item['subject']}]" if item.get("subject") else ""
        lines.append(
            f"{item['timestamp']} {item['level']:<8} "
            f"{item['event']}{subject} - {item['detail']}"
        )
    return "\n".join(lines)
