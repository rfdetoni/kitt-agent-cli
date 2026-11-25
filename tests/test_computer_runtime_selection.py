from unittest.mock import patch

import pytest

from kitt.history.database import HistoryDatabase
from kitt.runtime.conversation_runtime import (
    ContainerCliRuntimeDriver,
    ConversationRuntimeManager,
)
from kitt_protocol import RuntimeBackend


def test_auto_selects_only_configured_available_backends(tmp_path):
    db = HistoryDatabase(tmp_path)
    manager = ConversationRuntimeManager(tmp_path, "ws", "conv", db)
    try:
        config = {"auto_backends": ["PODMAN", "DOCKER"], "image": "alpine:3.20"}
        with patch("shutil.which", side_effect=lambda binary: binary if binary == "docker" else None):
            assert manager._select_auto_backend(config) == RuntimeBackend.DOCKER
        with pytest.raises(PermissionError):
            manager._select_auto_backend({"auto_backends": ["LOCAL"]})
        with pytest.raises(ValueError):
            manager._select_auto_backend({})
        with patch("shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="no permitted"):
                manager._select_auto_backend(config)
    finally:
        db.close()


def test_auto_never_retries_failed_provision_on_another_backend(tmp_path):
    db = HistoryDatabase(tmp_path)
    manager = ConversationRuntimeManager(tmp_path, "ws", "conv", db)
    try:
        with patch("shutil.which", return_value="/bin/runtime"), patch.object(
            manager.drivers[RuntimeBackend.PODMAN], "provision", side_effect=RuntimeError("failed")
        ) as first, patch.object(manager.drivers[RuntimeBackend.DOCKER], "provision") as second:
            with pytest.raises(RuntimeError, match="failed"):
                manager.provision("AUTO", {"auto_backends": ["PODMAN", "DOCKER"], "image": "alpine"})
            first.assert_called_once()
            second.assert_not_called()
    finally:
        db.close()


@pytest.mark.parametrize("backend,binary", [
    (RuntimeBackend.DOCKER, "docker"), (RuntimeBackend.PODMAN, "podman"),
])
def test_container_cold_pause_is_opt_in(tmp_path, backend, binary):
    driver = ContainerCliRuntimeDriver(backend, binary, tmp_path)
    with patch(
        "kitt.runtime.conversation_runtime._run_host", return_value={"success": True}
    ) as run:
        driver.pause("id", {})
        driver.resume("id", {})
        driver.pause("id", {"hibernate_on_pause": True})
        driver.resume("id", {"hibernate_on_pause": True})
        assert [call.args[0] for call in run.call_args_list] == [
            [binary, "pause", "id"], [binary, "unpause", "id"],
            [binary, "stop", "id"], [binary, "start", "id"],
        ]
