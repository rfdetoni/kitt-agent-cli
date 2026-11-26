from __future__ import annotations

import hashlib
import threading
import time
import uuid


class PersistentWakeScheduler:
    """Durable scheduler that re-enters conversations through the host queue."""

    def __init__(self, db, callback, *, poll_interval_seconds=1.0, event_callback=None, workspace_id=None):
        self.db = db
        self.callback = callback
        self.poll_interval = max(0.1, float(poll_interval_seconds))
        self.workspace_id = workspace_id
        self._on_event = event_callback or (lambda *_: None)
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.RLock()
        self._maintenance = None
        self._maintenance_interval = 30.0

    def set_maintenance(self, callback, *, interval_seconds=30.0):
        """Attach bounded host maintenance to the existing scheduler."""
        self._maintenance = callback
        self._maintenance_interval = max(30.0, float(interval_seconds))

    @staticmethod
    def _cron_interval(expr):
        if not expr:
            return None
        fields = str(expr).strip().split()
        if len(fields) != 5:
            raise ValueError("cron_expr supports five-field minute cron only")
        minute, hour, day, month, weekday = fields
        if (hour, day, month, weekday) != ("*", "*", "*", "*"):
            raise ValueError("cron_expr currently supports recurring minute cadence only")
        if minute == "*":
            return 60.0
        if minute.startswith("*/") and minute[2:].isdigit() and int(minute[2:]) > 0:
            return float(int(minute[2:]) * 60)
        raise ValueError("cron_expr must use '*' or '*/N' in the minute field")

    def schedule(self, *, workspace_id, conversation_id, prompt, run_at=None,
                 interval_seconds=None, cron_expr=None, task_id=None):
        text = str(prompt or "").strip()
        if not text:
            raise ValueError("scheduled prompt must be non-empty")
        cron_interval = self._cron_interval(cron_expr)
        interval = cron_interval if cron_interval is not None else interval_seconds
        if interval is not None:
            interval = max(1.0, float(interval))
        now = time.time()
        next_run = float(run_at) if run_at is not None else now + (interval or 0.0)
        if next_run < now and interval is None:
            next_run = now
        sid = str(task_id or f"schedule_{uuid.uuid4().hex}")
        with self.db.get_connection() as conn:
            conn.execute(
                """INSERT INTO scheduled_tasks(
                       id,workspace_id,conversation_id,cron_expr,interval_seconds,prompt,
                       state,last_run_at,next_run_at,created_at
                   ) VALUES(?,?,?,?,?,?,'ACTIVE',NULL,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                       workspace_id=excluded.workspace_id,
                       conversation_id=excluded.conversation_id,
                       cron_expr=excluded.cron_expr,
                       interval_seconds=excluded.interval_seconds,
                       prompt=excluded.prompt,
                       state='ACTIVE',
                       next_run_at=excluded.next_run_at""",
                (sid, workspace_id, conversation_id, cron_expr, interval, text, next_run, now),
            )
        self._on_event("ScheduledWakeUpdated", {"id": sid, "next_run_at": next_run})
        return sid

    def set_heartbeat(self, *, workspace_id, conversation_id, prompt, interval_seconds):
        digest = hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()[:16]
        return self.schedule(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            prompt=prompt,
            interval_seconds=max(5.0, float(interval_seconds)),
            task_id=f"heartbeat_{digest}",
        )

    def cancel(self, task_id, *, workspace_id=None):
        sql = "UPDATE scheduled_tasks SET state='CANCELLED' WHERE id=? AND state!='CANCELLED'"
        args = [task_id]
        if workspace_id is not None:
            sql += " AND workspace_id=?"
            args.append(workspace_id)
        with self.db.get_connection() as conn:
            cur = conn.execute(sql, args)
            return cur.rowcount > 0

    def list(self, conversation_id=None, limit=100, *, workspace_id=None):
        sql = "SELECT * FROM scheduled_tasks"
        clauses = []
        args = []
        if conversation_id:
            clauses.append("conversation_id=?")
            args.append(conversation_id)
        if workspace_id is not None:
            clauses.append("workspace_id=?")
            args.append(workspace_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY next_run_at ASC LIMIT ?"
        args.append(max(1, min(int(limit), 200)))
        with self.db.get_connection() as conn:
            return [dict(row) for row in conn.execute(sql, args).fetchall()]

    def run_due_once(self, *, now=None, limit=32, workspace_id=None):
        timestamp = time.time() if now is None else float(now)
        effective_workspace = workspace_id if workspace_id is not None else self.workspace_id
        claimed = []
        with self._lock:
            with self.db.get_connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                if effective_workspace is not None:
                    rows = conn.execute(
                        """SELECT * FROM scheduled_tasks
                           WHERE state='ACTIVE' AND workspace_id=? AND next_run_at<=?
                           ORDER BY next_run_at ASC LIMIT ?""",
                        (effective_workspace, timestamp, max(1, min(int(limit), 64))),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """SELECT * FROM scheduled_tasks
                           WHERE state='ACTIVE' AND next_run_at<=?
                           ORDER BY next_run_at ASC LIMIT ?""",
                        (timestamp, max(1, min(int(limit), 64))),
                    ).fetchall()
                for row in rows:
                    cur = conn.execute(
                        "UPDATE scheduled_tasks SET state='RUNNING' WHERE id=? AND state='ACTIVE'",
                        (row["id"],),
                    )
                    if cur.rowcount:
                        claimed.append(dict(row))

        for job in claimed:
            error = None
            try:
                self.callback(
                    job["conversation_id"],
                    job["prompt"],
                    {"schedule_id": job["id"], "scheduled_at": job["next_run_at"]},
                )
            except Exception as exc:
                error = str(exc)
            interval = job.get("interval_seconds")
            next_run = timestamp + float(interval) if interval else timestamp
            state = "ACTIVE" if interval else "COMPLETED"
            if error and interval:
                next_run = timestamp + max(float(interval), 5.0)
            with self.db.get_connection() as conn:
                conn.execute(
                    "UPDATE scheduled_tasks SET state=?,last_run_at=?,next_run_at=? WHERE id=?",
                    (state, timestamp, next_run, job["id"]),
                )
            self._on_event(
                "ScheduledWakeFired",
                {"id": job["id"], "state": state, "error": error},
            )
        return len(claimed)

    def _loop(self):
        next_maintenance = time.monotonic() + self._maintenance_interval
        while not self._stop.wait(self.poll_interval):
            try:
                self.run_due_once()
            except Exception as exc:
                self._on_event("ScheduledWakeError", {"error": str(exc)})
            if self._maintenance is not None and time.monotonic() >= next_maintenance:
                next_maintenance = time.monotonic() + self._maintenance_interval
                try:
                    self._maintenance()
                except Exception as exc:
                    self._on_event("RuntimeMaintenanceError", {"error": type(exc).__name__})

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            with self.db.get_connection() as conn:
                if self.workspace_id is not None:
                    conn.execute(
                        "UPDATE scheduled_tasks SET state='ACTIVE' WHERE state='RUNNING' AND workspace_id=?",
                        (self.workspace_id,),
                    )
                else:
                    conn.execute("UPDATE scheduled_tasks SET state='ACTIVE' WHERE state='RUNNING'")
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, name="kitt-wake-scheduler", daemon=True
            )
            self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._thread = None
