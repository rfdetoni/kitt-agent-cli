from types import SimpleNamespace

import pytest

from kitt.cli.operations import build_session_report
from kitt.history.database import HistoryDatabase
from kitt.history.repository import HistoryRepository
from kitt.runtime.conversation_runtime import ConversationRuntimeRegistry
from kitt.runtime.rpc import RuntimeRPC


def test_operator_runtime_lifecycle_and_session_report(tmp_path):
    db = HistoryDatabase(tmp_path)
    history = HistoryRepository(db)
    workspace = history.get_or_create_workspace(str(tmp_path))
    conversation = history.create_conversation(workspace["id"], "runtime")
    registry = ConversationRuntimeRegistry(tmp_path, workspace["id"], db)
    rpc = RuntimeRPC(SimpleNamespace(
        workspace_id=workspace["id"], history=SimpleNamespace(repo=history),
        conversation_runtimes=registry,
    ))
    params = {"conversation_id": conversation["id"]}
    try:
        assert rpc.dispatch("runtime.status", params)["state"] == "UNBOUND"
        binding = rpc.dispatch("runtime.provision", {
            **params, "backend": "LOCAL", "config": {"password": "secret"},
        })["binding"]
        assert binding["backend"] == "LOCAL"
        assert binding["state"] == "RUNNING"
        with pytest.raises(RuntimeError, match="already has"):
            rpc.dispatch("runtime.provision", {**params, "backend": "LOCAL"})
        assert rpc.dispatch("runtime.attach", {
            **params, "backend": "LOCAL", "runtime_id": binding["runtime_id"],
        })["binding"] == binding
        with pytest.raises(RuntimeError, match="different runtime"):
            rpc.dispatch("runtime.attach", {
                **params, "backend": "LOCAL", "runtime_id": "another",
            })
        assert rpc.dispatch("runtime.pause", params)["binding"]["state"] == "PAUSED"
        assert rpc.dispatch("runtime.resume", params)["binding"]["state"] == "RUNNING"
        assert rpc.dispatch("runtime.health", params)["ok"]
        rows = build_session_report(str(tmp_path), [conversation])
        assert rows[0]["runtime_binding"] == {
            "backend": "LOCAL", "state": "RUNNING", "runtime_id": binding["runtime_id"],
        }
        assert rpc.dispatch("runtime.terminate", params)["ok"]
        assert rpc.dispatch("runtime.status", params)["state"] == "UNBOUND"
        with pytest.raises(PermissionError):
            rpc.dispatch("runtime.status", {"conversation_id": "missing"})
        with pytest.raises(ValueError):
            rpc.dispatch("runtime.status", {})
        with pytest.raises(KeyError):
            rpc.dispatch("runtime.execute", params)
    finally:
        db.close()
