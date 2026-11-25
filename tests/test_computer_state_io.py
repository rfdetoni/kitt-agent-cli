from io import BytesIO
from unittest.mock import patch

import pytest

from kitt.history.database import HistoryDatabase
from kitt.history.repository import HistoryRepository
from kitt.runtime.conversation_runtime import RemoteRuntimeDriver
from kitt.runtime.state import RuntimeStateStore


def test_state_reads_do_not_write_or_delete_other_conversation_expirations(tmp_path):
    db = HistoryDatabase(tmp_path)
    history = HistoryRepository(db)
    workspace = history.get_or_create_workspace(str(tmp_path))
    first = history.create_conversation(workspace["id"], "first")
    second = history.create_conversation(workspace["id"], "second")
    one = RuntimeStateStore(db, workspace["id"], first["id"])
    two = RuntimeStateStore(db, workspace["id"], second["id"])
    try:
        one.set("expired", 1, ttl_seconds=-1)
        two.set("expired", 2, ttl_seconds=-1)
        with db.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            assert one.get("expired") is None
            assert one.list_keys() == []
            assert conn.execute("SELECT COUNT(*) FROM runtime_states").fetchone()[0] == 2
        one.set("active", 3)
        with db.get_connection() as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM runtime_states WHERE conversation_id=?",
                (second["id"],),
            ).fetchone()[0] == 1
        assert one.get("active") == 3
    finally:
        db.close()


def test_remote_response_rejects_oversized_valid_json_prefix():
    body = b'{}' + b' ' * (1024 * 1024)
    with patch("urllib.request.urlopen", return_value=BytesIO(body)):
        with pytest.raises(RuntimeError, match="exceeds"):
            RemoteRuntimeDriver().health("runtime", {"endpoint": "https://example.invalid"})


def test_remote_response_rejects_invalid_utf8():
    with patch("urllib.request.urlopen", return_value=BytesIO(b'\xff')):
        with pytest.raises(RuntimeError, match="UnicodeDecodeError"):
            RemoteRuntimeDriver().health("runtime", {"endpoint": "https://example.invalid"})
