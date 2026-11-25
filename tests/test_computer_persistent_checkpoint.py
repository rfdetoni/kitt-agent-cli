from types import SimpleNamespace

from kitt.runtime.persistent_program import PersistentProgramSessions


class State:
    def __init__(self):
        self.value = {"prior": 1}

    def get(self, key):
        return self.value

    def set(self, key, value):
        self.value = value


class Runtime:
    def execute(self, arguments, **kwargs):
        return SimpleNamespace(
            success=True, data={"state": {}, "state_truncated": True}
        )


def test_truncated_state_never_overwrites_checkpoint():
    state = State()
    sessions = PersistentProgramSessions(state, Runtime())
    result = sessions.execute(
        "default", {}, turn_id="t", origin="test",
        capabilities=set(), security_context=None,
    )
    assert not result.success
    assert state.value == {"prior": 1}


def test_concurrent_checkpoint_compare_and_set(tmp_path):
    from kitt.history.database import HistoryDatabase
    from kitt.history.repository import HistoryRepository
    from kitt.runtime.state import RuntimeStateStore

    db = HistoryDatabase(tmp_path)
    history = HistoryRepository(db)
    workspace = history.get_or_create_workspace(str(tmp_path))
    conversation = history.create_conversation(workspace["id"], "checkpoint")
    first = RuntimeStateStore(db, workspace["id"], conversation["id"])
    second = RuntimeStateStore(db, workspace["id"], conversation["id"])
    key = "program-session:default"
    try:
        assert first.set(key, {"value": 1}, expected_value=None)
        previous = first.get(key)
        assert second.set(key, {"value": 2}, expected_value=previous)
        assert not first.set(key, {"value": 3}, expected_value=previous)
        assert first.get(key) == {"value": 2}
    finally:
        db.close()


def test_session_reports_checkpoint_conflict(tmp_path):
    from kitt.history.database import HistoryDatabase
    from kitt.history.repository import HistoryRepository
    from kitt.runtime.state import RuntimeStateStore

    db = HistoryDatabase(tmp_path)
    history = HistoryRepository(db)
    workspace = history.get_or_create_workspace(str(tmp_path))
    conversation = history.create_conversation(workspace["id"], "checkpoint")
    state = RuntimeStateStore(db, workspace["id"], conversation["id"])
    key = "program-session:default"
    state.set(key, {"value": 1})

    class ConcurrentRuntime:
        def execute(self, arguments, **kwargs):
            state.set(key, {"value": 2})
            return SimpleNamespace(success=True, data={"state": {"value": 3}})

    try:
        sessions = PersistentProgramSessions(state, ConcurrentRuntime())
        result = sessions.execute(
            "default", {}, turn_id="t", origin="test",
            capabilities=set(), security_context=None,
        )
        assert not result.success
        assert result.data["state_conflict"]
        assert state.get(key) == {"value": 2}
    finally:
        db.close()
