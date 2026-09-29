import sqlite3
import threading
from pathlib import Path

from kitt.history.migrations import CURRENT_SCHEMA_STATEMENTS, CURRENT_SCHEMA_VERSION

SCHEMA_VERSION = CURRENT_SCHEMA_VERSION
CREATE_TABLES_SQL = "\n".join(CURRENT_SCHEMA_STATEMENTS)


class _FileConnection(sqlite3.Connection):
    """File-backed connection that closes when a context manager exits."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class _InMemoryConnectionContext:
    """Serialize transactions for the shared in-memory SQLite connection."""

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock):
        self._conn = conn
        self._lock = lock

    def __enter__(self) -> sqlite3.Connection:
        self._lock.acquire()
        return self._conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type:
                try:
                    self._conn.rollback()
                except Exception:
                    pass
            else:
                self._conn.commit()
        finally:
            self._lock.release()
        return False

    def __getattr__(self, name):
        return getattr(self._conn, name)


class HistoryDatabase:
    """SQLite database manager for persistent workspace conversation history."""

    def __init__(self, root_dir: str = ".", in_memory: bool = False):
        self.in_memory = in_memory or root_dir == ":memory:"
        self._mem_lock = threading.RLock()
        if self.in_memory:
            self.root_path = Path(".").resolve()
            self.db_path = ":memory:"
            self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._mem_conn.row_factory = sqlite3.Row
            self._mem_conn.execute("PRAGMA foreign_keys = ON;")
            self._init_memory_db()
        else:
            self.root_path = Path(root_dir).expanduser().resolve(strict=False)
            self.kitt_dir = self.root_path / ".kitt" / "history"
            self.kitt_dir.mkdir(parents=True, exist_ok=True)
            self.db_path = self.kitt_dir / "history.sqlite3"
            self._init_db()

    def _init_memory_db(self) -> None:
        from kitt.history.migrations import MigrationRunner

        with self._mem_lock:
            MigrationRunner().migrate(self._mem_conn)

    def get_connection(self):
        if self.in_memory:
            return _InMemoryConnectionContext(self._mem_conn, self._mem_lock)
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=10.0,
            factory=_FileConnection,
        )
        conn.row_factory = sqlite3.Row
        conn.executescript(
            "PRAGMA foreign_keys = ON;"
            " PRAGMA busy_timeout = 5000;"
            " PRAGMA synchronous = NORMAL;"
        )
        return conn

    def _init_db(self) -> None:
        from kitt.history.migrations import MigrationRunner

        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        try:
            conn.execute("PRAGMA journal_mode = WAL;")
            MigrationRunner().migrate(conn)
        finally:
            conn.close()

    def close(self) -> None:
        """Flush the WAL. Connections are short-lived and owned by callers."""
        if self.in_memory:
            with self._mem_lock:
                try:
                    self._mem_conn.close()
                except Exception:
                    pass
            return
        try:
            with self.get_connection() as conn:
                conn.execute("PRAGMA wal_checkpoint(PASSIVE);")
        except sqlite3.Error:
            pass
