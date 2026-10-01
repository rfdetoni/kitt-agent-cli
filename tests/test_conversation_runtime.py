from __future__ import annotations

from dataclasses import dataclass

import pytest

from kitt.history.database import HistoryDatabase
from kitt.runtime.conversation_runtime import ConversationRuntimeManager
from kitt_protocol import RuntimeBackend


@dataclass
class FakeRuntimeDriver:
    backend: RuntimeBackend
    counter: int = 0
    paused: set[str] | None = None

    def __post_init__(self):
        self.paused = set()

    def provision(self, config):
        self.counter += 1
        return f"{self.backend.value.lower()}-{self.counter}"

    def attach(self, runtime_id, config):
        return None

    def execute(self, runtime_id, argv, config):
        return {"success": True, "runtime_id": runtime_id, "argv": list(argv)}

    def pause(self, runtime_id, config):
        self.paused.add(runtime_id)

    def resume(self, runtime_id, config):
        self.paused.discard(runtime_id)

    def health(self, runtime_id, config):
        return {
            "ok": runtime_id not in self.paused,
            "runtime_id": runtime_id,
            "backend": self.backend.value,
        }

    def snapshot_state(self, runtime_id, config):
        return {"runtime_id": runtime_id, "backend": self.backend.value}

    def terminate(self, runtime_id, config):
        self.paused.discard(runtime_id)


@pytest.mark.parametrize("backend", [RuntimeBackend.DOCKER, RuntimeBackend.PODMAN])
def test_container_runtime_binding_provision_pause_resume_is_conversation_scoped(
    tmp_path, backend
):
    db = HistoryDatabase(str(tmp_path))
    driver = FakeRuntimeDriver(backend)
    manager = ConversationRuntimeManager(
        tmp_path,
        "workspace-1",
        "conversation-1",
        db,
        drivers={backend: driver},
    )

    binding = manager.provision(
        backend,
        {
            "image": "example/test:latest",
            "password": "must-not-persist",
            "token": "must-not-persist",
        },
    )
    assert binding.backend == backend
    assert binding.state == "RUNNING"

    paused = manager.pause()
    assert paused.state == "PAUSED"
    assert manager.health()["ok"] is False

    resumed = manager.resume()
    assert resumed.state == "RUNNING"
    assert manager.health()["ok"] is True

    config = manager._config()
    assert "password" not in config
    assert "token" not in config
    db.close()


def test_runtime_binding_and_workspace_state_survive_runtime_replacement(tmp_path):
    db = HistoryDatabase(str(tmp_path))
    driver = FakeRuntimeDriver(RuntimeBackend.DOCKER)
    drivers = {RuntimeBackend.DOCKER: driver}

    first_manager = ConversationRuntimeManager(
        tmp_path,
        "workspace-1",
        "conversation-1",
        db,
        drivers=drivers,
    )
    first = first_manager.provision(
        RuntimeBackend.DOCKER,
        {"image": "example/test:latest", "mount": "workspace"},
    )
    assert first.runtime_id == "docker-1"

    # A new manager instance represents reconnect/restart of the control plane.
    reconnected = ConversationRuntimeManager(
        tmp_path,
        "workspace-1",
        "conversation-1",
        db,
        drivers=drivers,
    )
    restored = reconnected.binding()
    assert restored is not None
    assert restored.runtime_id == "docker-1"
    assert restored.workspace_ref == str(tmp_path.resolve())

    replacement = reconnected.provision(
        RuntimeBackend.DOCKER,
        {"image": "example/test:next", "mount": "workspace"},
    )
    assert replacement.runtime_id == "docker-2"
    assert replacement.workspace_ref == restored.workspace_ref

    restarted_again = ConversationRuntimeManager(
        tmp_path,
        "workspace-1",
        "conversation-1",
        db,
        drivers=drivers,
    )
    final = restarted_again.binding()
    assert final is not None
    assert final.runtime_id == "docker-2"
    assert final.state == "RUNNING"
    db.close()
