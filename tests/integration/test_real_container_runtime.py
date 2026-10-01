from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from kitt.history.database import HistoryDatabase
from kitt.runtime.conversation_runtime import ConversationRuntimeManager
from kitt_protocol import RuntimeBackend


_RUNTIME_NAME = os.getenv("KITT_CONTAINER_RUNTIME", "").strip().lower()
if _RUNTIME_NAME not in {"docker", "podman"}:
    pytestmark = pytest.mark.skip(
        reason="set KITT_CONTAINER_RUNTIME=docker|podman for real runtime integration"
    )


def _backend() -> RuntimeBackend:
    return RuntimeBackend.DOCKER if _RUNTIME_NAME == "docker" else RuntimeBackend.PODMAN


def test_real_container_lifecycle_replacement_persistence_and_secret_sanitization(
    tmp_path: Path,
):
    binary = shutil.which(_RUNTIME_NAME)
    assert binary, f"{_RUNTIME_NAME} binary is required by the integration workflow"

    db = HistoryDatabase(str(tmp_path))
    conversation_id = f"real-{_RUNTIME_NAME}"
    manager = ConversationRuntimeManager(
        tmp_path, "workspace-real", conversation_id, db
    )
    try:
        first = manager.provision(
            _backend(),
            {
                "image": os.getenv("KITT_RUNTIME_TEST_IMAGE", "alpine:3.20"),
                "password": "fake-password-must-not-persist",
                "token": "fake-token-must-not-persist",
            },
        )
        assert manager.health()["ok"] is True

        reconnected = ConversationRuntimeManager(
            tmp_path, "workspace-real", conversation_id, db
        )
        restored = reconnected.binding()
        assert restored is not None
        assert restored.runtime_id == first.runtime_id
        reconnected.attach(
            _backend(), first.runtime_id, {"image": "alpine:3.20"}
        )

        created = reconnected.execute(["touch", "runtime-state.txt"])
        assert created["success"] is True
        assert (tmp_path / "runtime-state.txt").exists()

        assert reconnected.pause().state == "PAUSED"
        assert reconnected.health()["ok"] is True
        assert reconnected.resume().state == "RUNNING"
        assert reconnected.health()["ok"] is True
        assert reconnected.snapshot_state()["healthy"] is True

        clean_config = reconnected._config()
        assert "password" not in clean_config
        assert "token" not in clean_config

        reconnected.terminate()
        replacement = reconnected.provision(
            _backend(),
            {"image": os.getenv("KITT_RUNTIME_TEST_IMAGE", "alpine:3.20")},
        )
        assert reconnected.execute(["test", "-f", "runtime-state.txt"])["success"] is True

        restarted = ConversationRuntimeManager(
            tmp_path, "workspace-real", conversation_id, db
        )
        binding = restarted.binding()
        assert binding is not None
        assert binding.runtime_id == replacement.runtime_id
        assert binding.workspace_ref == str(tmp_path.resolve())
        restarted.terminate()
    finally:
        try:
            manager.terminate()
        except Exception:
            pass
        db.close()
