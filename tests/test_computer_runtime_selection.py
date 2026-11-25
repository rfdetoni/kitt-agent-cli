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

def test_runtime_host_capture_is_bounded(tmp_path):
    import sys
    from kitt.runtime.conversation_runtime import _run_host

    result = _run_host(
        [sys.executable, "-c", "import sys; sys.stdout.write('x' * 2000000); print('END')"],
        cwd=tmp_path,
    )
    assert result["success"]
    assert result["truncated"]
    assert result["stdout_total_bytes"] == 2000004
    assert len(result["stdout"].encode("utf-8")) <= 256 * 1024
    assert result["stdout"].endswith("END\n")


def test_container_rejects_option_like_runtime_id_and_image(tmp_path):
    driver = ContainerCliRuntimeDriver(RuntimeBackend.DOCKER, "docker", tmp_path)
    with patch("kitt.runtime.conversation_runtime._run_host") as execute:
        with pytest.raises(ValueError):
            driver.provision({"runtime_id": "--privileged", "image": "alpine"})
        with pytest.raises(ValueError):
            driver.provision({"image": "--privileged"})
        execute.assert_not_called()
