import json
import uuid
import time
import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, List, Dict, Any, Optional
from kitt.history.database import HistoryDatabase

if TYPE_CHECKING:
    from kitt.core.pending_action import PendingAction

def json_dumps(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

def canonical_workspace_path(root_path: str | Path) -> str:
    return str(Path(root_path).expanduser().resolve(strict=False))

def _workspace_row(conn, path_hash: str) -> Optional[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM workspaces WHERE canonical_path_hash = ?;", (path_hash,))
    row = cur.fetchone()
    return dict(row) if row else None

def _create_workspace_row(conn, canon: str, path_hash: str) -> Dict[str, Any]:
    ws_id = uuid.uuid4().hex
    display_name = str(Path(canon).name) or "workspace"
    now = time.time()
    conn.execute(
        "INSERT INTO workspaces (id, canonical_path_hash, display_name, git_root, created_at, last_opened_at) VALUES (?, ?, ?, ?, ?, ?);",
        (ws_id, path_hash, display_name, canon, now, now)
    )
    return {
        "id": ws_id,
        "canonical_path_hash": path_hash,
        "display_name": display_name,
        "git_root": canon,
        "created_at": now,
        "last_opened_at": now
    }

def resolve_workspace_identity(db: HistoryDatabase, root_path: str | Path):
    """Resolve (or create) the persisted workspace identity for a canonical root."""
    from kitt.core.workspace_identity import WorkspaceIdentity

    canon = canonical_workspace_path(root_path)
    path_hash = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    with db.get_connection() as conn:
        row = _workspace_row(conn, path_hash)
        if row:
            conn.execute("UPDATE workspaces SET last_opened_at = ? WHERE id = ?;", (time.time(), row["id"]))
            ws = row
        else:
            ws = _create_workspace_row(conn, canon, path_hash)
    return WorkspaceIdentity(id=ws["id"], canonical_root=Path(canon), canonical_path_hash=ws["canonical_path_hash"])

def get_or_create_workspace_identity(root_path: str | Path):
    """Backward-compatible helper used by WorkspaceIdentity.build without a db.

    Uses a transient connection against the default on-disk location.  Prefer
    ``resolve_workspace_identity`` when a database handle already exists.
    """
    from kitt.history.database import HistoryDatabase
    from kitt.history.migrations import MigrationRunner

    canon = canonical_workspace_path(root_path)
    path_hash = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    import sqlite3
    db_path = Path(canon) / ".kitt" / "history" / "history.sqlite3"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        MigrationRunner().migrate(conn)
        conn.commit()
        row = _workspace_row(conn, path_hash)
        if row:
            conn.execute("UPDATE workspaces SET last_opened_at = ? WHERE id = ?;", (time.time(), row["id"]))
            ws = row
        else:
            ws = _create_workspace_row(conn, canon, path_hash)
        conn.commit()
    finally:
        conn.close()
    from kitt.core.workspace_identity import WorkspaceIdentity
    return WorkspaceIdentity(id=ws["id"], canonical_root=Path(canon), canonical_path_hash=ws["canonical_path_hash"])

class HistoryRepository:
    """Data access repository for SQLite history entities."""

    def __init__(self, db: HistoryDatabase):
        self.db = db

    def get_or_create_workspace(self, root_path: str) -> Dict[str, Any]:
        canon = canonical_workspace_path(root_path)
        path_hash = hashlib.sha256(canon.encode('utf-8')).hexdigest()
        display_name = str(Path(canon).name) or "workspace"
        now = time.time()

        with self.db.get_connection() as conn:
            row = _workspace_row(conn, path_hash)
            if row:
                conn.execute("UPDATE workspaces SET last_opened_at = ? WHERE id = ?;", (now, row["id"]))
                return row
            ws = _create_workspace_row(conn, canon, path_hash)
            return ws

    def create_conversation(self, workspace_id: str, title: str = "New Conversation", parent_id: Optional[str] = None) -> Dict[str, Any]:
        conv_id = uuid.uuid4().hex
        now = time.time()
        with self.db.get_connection() as conn:
            conn.execute(
                """INSERT INTO conversations 
                (id, workspace_id, title, status, parent_conversation_id, created_at, updated_at) 
                VALUES (?, ?, ?, 'ACTIVE', ?, ?, ?);""",
                (conv_id, workspace_id, title, parent_id, now, now)
            )
        return self.get_conversation(conv_id)

    def get_conversation(self, conv_id: str) -> Optional[Dict[str, Any]]:
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM conversations WHERE id = ?;", (conv_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def fork_conversation(self, conv_id: str, new_title: Optional[str] = None) -> Optional[Dict[str, Any]]:
        orig_conv = self.get_conversation(conv_id)
        if not orig_conv:
            return None

        title = new_title or f"Fork of {orig_conv['title']}"
        new_conv = self.create_conversation(orig_conv["workspace_id"], title=title, parent_id=conv_id)
        new_conv_id = new_conv["id"]

        msgs = self.get_messages_for_conversation(conv_id)
        # Prepare rows outside the write transaction, reuse persisted hashes,
        # then batch both inserts. This keeps the WAL lock short even for long
        # conversations and avoids one UPDATE per cloned message.
        turn_rows = []
        message_rows = []
        last_created_at = new_conv["updated_at"]
        for ordinal, msg in enumerate(msgs, start=1):
            new_turn_id = f"fork_{new_conv_id[:8]}_{ordinal}"
            created_at = time.time()
            last_created_at = created_at
            content_hash = msg.get("content_hash") or hashlib.sha256(
                msg["content"].encode("utf-8")
            ).hexdigest()
            turn_rows.append((new_turn_id, new_conv_id, ordinal, created_at))
            message_rows.append((
                uuid.uuid4().hex,
                new_conv_id,
                new_turn_id,
                msg["role"],
                msg["content"],
                created_at,
                msg.get("token_count", 0),
                content_hash,
            ))

        if turn_rows:
            with self.db.get_connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.executemany(
                    "INSERT INTO turns (id, conversation_id, ordinal, started_at) VALUES (?, ?, ?, ?);",
                    turn_rows,
                )
                conn.executemany(
                    """INSERT INTO messages
                    (id, conversation_id, turn_id, role, content, created_at, token_count, content_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?);""",
                    message_rows,
                )
                conn.execute(
                    "UPDATE conversations SET updated_at = ? WHERE id = ?;",
                    (last_created_at, new_conv_id),
                )
        return new_conv

    def list_conversations(self, workspace_id: str, limit: int = 20, offset: int = 0, search: Optional[str] = None) -> List[Dict[str, Any]]:
        MAX_PAGE_SIZE = 100
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        offset = max(0, offset)
        
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            if search:
                term = f"%{search}%"
                cur.execute(
                    "SELECT * FROM conversations WHERE workspace_id = ? AND status NOT IN ('DELETED','INTERNAL_CHILD') AND (title LIKE ? OR compact_summary LIKE ?) ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?;",
                    (workspace_id, term, term, limit, offset)
                )
            else:
                cur.execute(
                    "SELECT * FROM conversations WHERE workspace_id = ? AND status NOT IN ('DELETED','INTERNAL_CHILD') ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?;",
                    (workspace_id, limit, offset)
                )
            return [dict(row) for row in cur.fetchall()]

    def delete_conversation(self, conv_id: str) -> bool:
        with self.db.get_connection() as conn:
            cur = conn.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
            return cur.rowcount > 0

    def update_conversation(self, conv_id: str, **kwargs) -> bool:
        if not kwargs:
            return False
        kwargs["updated_at"] = time.time()
        keys = ", ".join(f"{k} = ?" for k in kwargs.keys())
        values = list(kwargs.values()) + [conv_id]
        with self.db.get_connection() as conn:
            conn.execute(f"UPDATE conversations SET {keys} WHERE id = ?;", values)
        return True

    def save_message(self, conv_id: str, turn_id: str, role: str, content: str, token_count: int = 0) -> str:
        from kitt.history.redaction import redact
        msg_id = uuid.uuid4().hex
        now = time.time()

        # Redaction: Centralized redaction before persisting
        content = redact(content)

        c_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()
        with self.db.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT id FROM turns WHERE id = ?", (turn_id,)).fetchone()
            if not row:
                ordinal = conn.execute(
                    "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM turns WHERE conversation_id = ?",
                    (conv_id,)
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO turns (id, conversation_id, ordinal, started_at) VALUES (?, ?, ?, ?);",
                    (turn_id, conv_id, ordinal, now)
                )
            conn.execute(
                """INSERT INTO messages 
                (id, conversation_id, turn_id, role, content, created_at, token_count, content_hash) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?);""",
                (msg_id, conv_id, turn_id, role, content, now, token_count, c_hash)
            )
            conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?;", (now, conv_id))
            entry_type = ("USER_MESSAGE" if role == "user"
                          else ("ASSISTANT_MESSAGE" if role == "assistant" else "TOOL_RESULT"))
            payload = {"role": role, "content": content, "message_id": msg_id}
            payload_json = json_dumps(payload)
            entry_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
            entry_id = f"entry_{uuid.uuid4().hex}"
            parent = conn.execute(
                "SELECT active_entry_id FROM conversations WHERE id = ?", (conv_id,)
            ).fetchone()
            parent_entry_id = parent["active_entry_id"] if parent else None
            generation = 0
            if parent_entry_id:
                gen = conn.execute(
                    "SELECT generation FROM session_entries WHERE id = ?", (parent_entry_id,)
                ).fetchone()
                generation = (gen["generation"] + 1) if gen else 0
            conn.execute(
                """INSERT INTO session_entries
                (id, conversation_id, parent_entry_id, turn_id, entry_type, payload_json,
                 include_in_context, generation, created_at, content_hash)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)""",
                (entry_id, conv_id, parent_entry_id, turn_id, entry_type,
                 payload_json, generation, now, entry_hash),
            )
            conn.execute(
                """UPDATE conversations SET active_entry_id = ?, active_generation = ?,
                   updated_at = ? WHERE id = ?""",
                (entry_id, generation, now, conv_id),
            )
        return msg_id
        
    def save_telemetry(self, conv_id: str, turn_id: str, route: str, start_time: float, duration_ms: float, input_tokens: int, output_tokens: int, tokens_saved: int) -> str:
        telemetry_id = uuid.uuid4().hex
        with self.db.get_connection() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO telemetry_events
                (id, conversation_id, turn_id, route, start_time, duration_ms, input_tokens, output_tokens, tokens_saved)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);""",
                (telemetry_id, conv_id, turn_id, route, start_time, duration_ms, input_tokens, output_tokens, tokens_saved)
            )
        return telemetry_id

    def save_tool_gain(
        self,
        conv_id: str,
        turn_id: str,
        tool_name: str,
        start_time: float,
        duration_ms: float,
        raw_tokens: int,
        output_tokens: int,
        tokens_saved: int,
    ) -> Optional[str]:
        """Persist tool-output savings without introducing a second metrics table.

        ``input_tokens`` stores the naive/raw tool-output estimate and
        ``output_tokens`` stores what was actually returned to the model.
        The turn FK is checked first because some non-conversation tool uses are
        intentionally ephemeral.
        """
        telemetry_id = uuid.uuid4().hex
        safe_tool = str(tool_name or "unknown").replace("\n", " ").replace("\r", " ")[:160]
        route = f"tool_gain:{safe_tool}"
        with self.db.get_connection() as conn:
            turn = conn.execute(
                "SELECT 1 FROM turns WHERE id = ? AND conversation_id = ?",
                (turn_id, conv_id),
            ).fetchone()
            if not turn:
                return None
            conn.execute(
                """INSERT INTO telemetry_events
                (id, conversation_id, turn_id, route, start_time, duration_ms, input_tokens, output_tokens, tokens_saved)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);""",
                (
                    telemetry_id,
                    conv_id,
                    turn_id,
                    route,
                    float(start_time),
                    max(0.0, float(duration_ms)),
                    max(0, int(raw_tokens)),
                    max(0, int(output_tokens)),
                    max(0, int(tokens_saved)),
                ),
            )
        return telemetry_id

    def get_telemetry_stats(self, conv_id: Optional[str] = None) -> Dict[str, Any]:
        query = """SELECT COUNT(*) as count,
                          COALESCE(SUM(input_tokens), 0) as input,
                          COALESCE(SUM(output_tokens), 0) as output,
                          COALESCE(SUM(tokens_saved), 0) as saved,
                          COALESCE(SUM(duration_ms), 0.0) as duration
                   FROM telemetry_events
                   WHERE NOT (route >= ? AND route < ?)"""
        args = ["tool_gain:", "tool_gain;"]
        if conv_id:
            query += " AND conversation_id = ?"
            args.append(conv_id)

        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(query, tuple(args))
            row = cur.fetchone()
            if not row or row["count"] == 0:
                return {"count": 0, "input": 0, "output": 0, "saved": 0, "duration": 0.0}
            return dict(row)

    def _gain_where(self, conv_id: Optional[str]) -> tuple[str, list[Any]]:
        # Prefix range is equivalent to LIKE 'tool_gain:%' for the generated
        # route format, while remaining directly indexable by SQLite.
        where = "route >= ? AND route < ?"
        args: list[Any] = ["tool_gain:", "tool_gain;"]
        if conv_id:
            where += " AND conversation_id = ?"
            args.append(conv_id)
        return where, args

    def get_gain_summary(self, conv_id: Optional[str] = None) -> Dict[str, Any]:
        where, args = self._gain_where(conv_id)
        query = f"""SELECT COUNT(*) as count,
                           COALESCE(SUM(input_tokens), 0) as raw,
                           COALESCE(SUM(output_tokens), 0) as output,
                           COALESCE(SUM(tokens_saved), 0) as saved,
                           COALESCE(SUM(duration_ms), 0.0) as duration
                    FROM telemetry_events
                    WHERE {where}"""
        with self.db.get_connection() as conn:
            row = conn.execute(query, tuple(args)).fetchone()
            if not row:
                return {"count": 0, "raw": 0, "output": 0, "saved": 0, "duration": 0.0}
            return dict(row)

    def get_gain_by_tool(
        self,
        conv_id: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 100))
        where, args = self._gain_where(conv_id)
        query = f"""SELECT SUBSTR(route, 11) as tool,
                           COUNT(*) as count,
                           COALESCE(SUM(input_tokens), 0) as raw,
                           COALESCE(SUM(output_tokens), 0) as output,
                           COALESCE(SUM(tokens_saved), 0) as saved,
                           COALESCE(SUM(duration_ms), 0.0) as duration
                    FROM telemetry_events
                    WHERE {where}
                    GROUP BY route
                    ORDER BY saved DESC, count DESC, tool ASC
                    LIMIT ?"""
        with self.db.get_connection() as conn:
            rows = conn.execute(query, (*args, limit)).fetchall()
            return [dict(row) for row in rows]

    def get_gain_history(
        self,
        conv_id: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        where, args = self._gain_where(conv_id)
        query = f"""SELECT SUBSTR(route, 11) as tool,
                           input_tokens as raw,
                           output_tokens as output,
                           tokens_saved as saved,
                           duration_ms as duration,
                           start_time
                    FROM telemetry_events
                    WHERE {where}
                    ORDER BY start_time DESC, id DESC
                    LIMIT ?"""
        with self.db.get_connection() as conn:
            rows = conn.execute(query, (*args, limit)).fetchall()
            return [dict(row) for row in rows]

    def get_gain_daily(
        self,
        conv_id: Optional[str] = None,
        days: int = 30,
    ) -> List[Dict[str, Any]]:
        days = max(1, min(int(days), 365))
        where, args = self._gain_where(conv_id)
        cutoff = time.time() - (days * 86400)
        query = f"""SELECT DATE(start_time, 'unixepoch', 'localtime') as day,
                           COUNT(*) as count,
                           COALESCE(SUM(input_tokens), 0) as raw,
                           COALESCE(SUM(output_tokens), 0) as output,
                           COALESCE(SUM(tokens_saved), 0) as saved
                    FROM telemetry_events
                    WHERE {where} AND start_time >= ?
                    GROUP BY day
                    ORDER BY day ASC"""
        with self.db.get_connection() as conn:
            rows = conn.execute(query, (*args, cutoff)).fetchall()
            return [dict(row) for row in rows]

    @staticmethod
    def _edit_feedback_key(
        provider: str,
        model: str,
        language: str,
        project_type: str,
    ) -> tuple[str, str, str, str]:
        return (
            str(provider or "").strip().casefold()[:128],
            str(model or "").strip()[:256],
            str(language or "").strip().casefold()[:64],
            str(project_type or "").strip().casefold()[:64],
        )

    def get_edit_strategy_feedback(
        self,
        *,
        workspace_id: str,
        provider: str,
        model: str,
        language: str,
        project_type: str,
    ) -> Dict[str, Dict[str, Any]]:
        provider, model, language, project_type = self._edit_feedback_key(
            provider, model, language, project_type
        )
        with self.db.get_connection() as conn:
            rows = conn.execute(
                """SELECT strategy, attempts, successes, parse_failures,
                          apply_failures, validation_failures, repair_required,
                          rollbacks, files_changed, output_tokens, latency_ms,
                          updated_at
                   FROM edit_strategy_feedback
                   WHERE workspace_id = ? AND provider = ? AND model = ?
                     AND language = ? AND project_type = ?""",
                (workspace_id, provider, model, language, project_type),
            ).fetchall()

        now = time.time()
        half_life_seconds = 30.0 * 86400.0
        metric_names = (
            "attempts",
            "successes",
            "parse_failures",
            "apply_failures",
            "validation_failures",
            "repair_required",
            "rollbacks",
            "files_changed",
            "output_tokens",
            "latency_ms",
        )
        result: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            item = dict(row)
            age = max(0.0, now - float(item.get("updated_at") or now))
            decay = 2.0 ** (-(age / half_life_seconds))
            for name in metric_names:
                item[name] = max(0.0, float(item.get(name, 0.0) or 0.0)) * decay
            result[str(item["strategy"])] = item
        return result

    def record_edit_strategy_feedback(
        self,
        *,
        workspace_id: str,
        provider: str,
        model: str,
        language: str,
        project_type: str,
        strategy: str,
        success: bool,
        failure_kind: str = "",
        repair_required: bool = False,
        rollback: bool = False,
        files_changed: int = 0,
        output_tokens: int = 0,
        latency_ms: float = 0.0,
    ) -> Dict[str, Any]:
        provider, model, language, project_type = self._edit_feedback_key(
            provider, model, language, project_type
        )
        strategy = str(strategy or "").strip().casefold()[:64]
        if not strategy:
            raise ValueError("edit strategy is required")
        failure_kind = str(failure_kind or "").strip().casefold()
        now = time.time()
        half_life_seconds = 30.0 * 86400.0
        metric_names = (
            "attempts",
            "successes",
            "parse_failures",
            "apply_failures",
            "validation_failures",
            "repair_required",
            "rollbacks",
            "files_changed",
            "output_tokens",
            "latency_ms",
        )
        with self.db.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT attempts, successes, parse_failures, apply_failures,
                          validation_failures, repair_required, rollbacks,
                          files_changed, output_tokens, latency_ms, updated_at
                   FROM edit_strategy_feedback
                   WHERE workspace_id = ? AND provider = ? AND model = ?
                     AND language = ? AND project_type = ? AND strategy = ?""",
                (workspace_id, provider, model, language, project_type, strategy),
            ).fetchone()
            current = {name: 0.0 for name in metric_names}
            if row is not None:
                age = max(0.0, now - float(row["updated_at"] or now))
                decay = 2.0 ** (-(age / half_life_seconds))
                for name in metric_names:
                    current[name] = max(0.0, float(row[name] or 0.0)) * decay

            current["attempts"] += 1.0
            current["successes"] += 1.0 if success else 0.0
            failure_column = {
                "parse_failure": "parse_failures",
                "apply_failure": "apply_failures",
                "validation_failure": "validation_failures",
            }.get(failure_kind)
            if failure_column:
                current[failure_column] += 1.0
            current["repair_required"] += 1.0 if repair_required else 0.0
            current["rollbacks"] += 1.0 if rollback else 0.0
            current["files_changed"] += max(0, int(files_changed or 0))
            current["output_tokens"] += max(0, int(output_tokens or 0))
            current["latency_ms"] += max(0.0, float(latency_ms or 0.0))

            conn.execute(
                """INSERT INTO edit_strategy_feedback
                   (workspace_id, provider, model, language, project_type, strategy,
                    attempts, successes, parse_failures, apply_failures,
                    validation_failures, repair_required, rollbacks, files_changed,
                    output_tokens, latency_ms, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(workspace_id, provider, model, language, project_type, strategy)
                   DO UPDATE SET attempts=excluded.attempts,
                                 successes=excluded.successes,
                                 parse_failures=excluded.parse_failures,
                                 apply_failures=excluded.apply_failures,
                                 validation_failures=excluded.validation_failures,
                                 repair_required=excluded.repair_required,
                                 rollbacks=excluded.rollbacks,
                                 files_changed=excluded.files_changed,
                                 output_tokens=excluded.output_tokens,
                                 latency_ms=excluded.latency_ms,
                                 updated_at=excluded.updated_at""",
                (
                    workspace_id,
                    provider,
                    model,
                    language,
                    project_type,
                    strategy,
                    current["attempts"],
                    current["successes"],
                    current["parse_failures"],
                    current["apply_failures"],
                    current["validation_failures"],
                    current["repair_required"],
                    current["rollbacks"],
                    current["files_changed"],
                    current["output_tokens"],
                    current["latency_ms"],
                    now,
                ),
            )
        return {**current, "strategy": strategy, "updated_at": now}

    def get_messages_for_conversation(self, conv_id: str) -> List[Dict[str, Any]]:
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """SELECT m.*
                   FROM messages AS m
                   JOIN turns AS t
                     ON t.id = m.turn_id
                    AND t.conversation_id = m.conversation_id
                   WHERE m.conversation_id = ?
                   ORDER BY t.ordinal ASC, m.created_at ASC, m.rowid ASC;""",
                (conv_id,),
            )
            return [dict(row) for row in cur.fetchall()]

    def save_pending_action(self, pa: 'PendingAction'):
        import json
        with self.db.get_connection() as conn:
            conn.execute(
                """INSERT INTO pending_actions
                (id, approval_request_id, turn_id, conversation_id, workspace_id, tool_name, 
                 normalized_args_json, action_hash, source_response_sha256, affected_paths_json, 
                 before_hashes_json, created_at, expires_at, state, security_context_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (pa.id, pa.approval_request_id, pa.turn_id, pa.conversation_id, pa.workspace_id, pa.tool_name,
                 json.dumps(pa.normalized_args), pa.action_hash, pa.source_response_sha256, 
                 json.dumps(pa.affected_paths), json.dumps(pa.before_hashes), pa.created_at, pa.expires_at, pa.state,
                 json.dumps(pa.security_context or {}))
            )

    def get_valid_pending_action(self, action_id: str, workspace_id: str) -> Optional['PendingAction']:
        import json, time
        from kitt.core.pending_action import PendingAction
        
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM pending_actions WHERE id = ? AND workspace_id = ? AND state = 'pending';", 
                (action_id, workspace_id)
            )
            row = cur.fetchone()
            if not row:
                return None
                
            if time.time() > row["expires_at"]:
                conn.execute("UPDATE pending_actions SET state = 'expired' WHERE id = ?;", (action_id,))
                return None
                
            return PendingAction(
                id=row["id"],
                approval_request_id=row["approval_request_id"],
                turn_id=row["turn_id"],
                conversation_id=row["conversation_id"],
                workspace_id=row["workspace_id"],
                tool_name=row["tool_name"],
                normalized_args=json.loads(row["normalized_args_json"]),
                action_hash=row["action_hash"],
                source_response_sha256=row["source_response_sha256"],
                affected_paths=json.loads(row["affected_paths_json"]),
                before_hashes=json.loads(row["before_hashes_json"]),
                created_at=row["created_at"],
                expires_at=row["expires_at"],
                state=row["state"],
                security_context=json.loads(row["security_context_json"] or "{}")
            )
            
    def consume_pending_action(self, action_id: str) -> bool:
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE pending_actions SET state = 'consumed' WHERE id = ? AND state = 'pending';", (action_id,))
            return cur.rowcount > 0

    def cancel_pending_action(self, action_id: str) -> bool:
        with self.db.get_connection() as conn:
            cur = conn.execute(
                "UPDATE pending_actions SET state='cancelled' WHERE id=? AND state='pending'",
                (action_id,),
            )
            return cur.rowcount > 0
