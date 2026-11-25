from kitt.history.database import HistoryDatabase
from kitt.runtime.safe_runtime import SafeRuntime
from kitt.runtime.state import RuntimeStateStore
from kitt.security.capabilities import CAP_REPO_READ, CAP_REPO_WRITE


def test_generic_state_cannot_access_operator_runtime_binding_or_checkpoints(tmp_path):
    db = HistoryDatabase(tmp_path)
    store = RuntimeStateStore(db, "workspace", "conversation")
    runtime = SafeRuntime(workspace_root=tmp_path, workspace_id="workspace",
                          conversation_id="conversation", state_store=store)
    try:
        caps = {CAP_REPO_READ, CAP_REPO_WRITE}
        for key in ("conversation.runtime.binding", "conversation.runtime.config",
                    "program-session:default"):
            store.set(key, {"trusted": True})
            for operation in ("state.get", "state.set"):
                result = runtime.execute(operation, {"key": key, "value": {"trusted": False}},
                                         effective_capabilities=caps)
                assert not result.success
                assert "reserved runtime state key" in result.error
            assert store.get(key) == {"trusted": True}
        result = runtime.execute("state.set", {"key": "ordinary", "value": 1},
                                 effective_capabilities=caps)
        assert result.success
        keys = runtime.execute("state.list", {}, effective_capabilities=caps)
        assert keys.success
        assert [item["key"] for item in keys.data] == ["ordinary"]
    finally:
        db.close()
