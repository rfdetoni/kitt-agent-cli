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


def test_container_resource_limits_are_enforced_by_engine_flags(tmp_path):
    driver = ContainerCliRuntimeDriver(RuntimeBackend.DOCKER, "docker", tmp_path)
    with patch("kitt.runtime.conversation_runtime._run_host", return_value={"success": True}) as run:
        driver.provision({"image": "alpine", "cpu_millis": 500, "memory_bytes": 67108864,
                          "pids_limit": 32, "network_disabled": True})
        argv = run.call_args.args[0]
        for flag, expected in [
            ("--cpus", "0.500"), ("--memory", "67108864"),
            ("--memory-swap", "67108864"), ("--pids-limit", "32"), ("--network", "none"),
        ]:
            assert argv[argv.index(flag) + 1] == expected
        with pytest.raises(ValueError):
            driver.provision({"image": "alpine", "memory_bytes": -1})
        assert run.call_count == 1


def test_auto_rejects_insufficient_local_capacity(tmp_path):
    db = HistoryDatabase(tmp_path)
    manager = ConversationRuntimeManager(tmp_path, "ws", "conv", db)
    config = {"auto_backends": ["PODMAN", "DOCKER", "REMOTE"],
              "image": "alpine", "endpoint": "https://example.invalid",
              "cpu_millis": 2000, "memory_bytes": 268435456}
    try:
        with patch("shutil.which", return_value="/bin/runtime"), patch(
            "kitt.runtime.conversation_runtime._local_capacity", return_value=(1000, 134217728)
        ):
            assert manager._select_auto_backend(config) == RuntimeBackend.REMOTE
        with pytest.raises(ValueError, match="LOCAL"):
            manager.provision("LOCAL", config)
    finally:
        db.close()


def test_execution_receipt_prevents_replay_and_conflicting_arguments(tmp_path):
    from kitt.runtime.conversation_runtime import LocalRuntimeDriver

    db = HistoryDatabase(tmp_path)
    driver = LocalRuntimeDriver(tmp_path)
    manager = ConversationRuntimeManager(tmp_path, "ws", "conv", db,
                                         drivers={RuntimeBackend.LOCAL: driver})
    try:
        manager.provision("LOCAL")
        with patch.object(driver, "execute", return_value={"success": True, "returncode": 0}) as execute:
            first = manager.execute(["echo", "a"], request_id="r1")
            second = manager.execute(["echo", "a"], request_id="r1")
            assert first["receipt"] == second["receipt"]
            assert second["replayed"]
            execute.assert_called_once()
            with pytest.raises(ValueError, match="different arguments"):
                manager.execute(["echo", "b"], request_id="r1")
        with patch.object(driver, "execute", side_effect=TimeoutError("uncertain")) as execute:
            with pytest.raises(TimeoutError):
                manager.execute(["echo", "a"], request_id="r2")
            operation = manager.operation_status()
            assert operation["active"]
            with pytest.raises(RuntimeError, match="recovery required"):
                manager.execute(["echo", "a"], request_id="r2")
            manager.acknowledge_interrupted_operation(operation["id"])
            assert manager.execute(["echo", "a"], request_id="r2")["receipt"]["status"] == "INDETERMINATE"
            execute.assert_called_once()
    finally:
        db.close()


def test_idle_container_hibernates_and_resumes_on_execution(tmp_path):
    db = HistoryDatabase(tmp_path)
    manager = ConversationRuntimeManager(tmp_path, "ws", "conv", db)
    driver = manager.drivers[RuntimeBackend.DOCKER]
    try:
        with patch.object(driver, "provision", return_value="test-id"), patch.object(
            driver, "pause"
        ) as pause, patch.object(driver, "resume") as resume, patch.object(
            driver, "execute", return_value={"success": True}
        ):
            binding = manager.provision("DOCKER", {
                "image": "alpine", "hibernate_on_pause": True,
                "idle_hibernate_seconds": 30, "auto_resume": True,
            })
            assert not manager.hibernate_idle(now_ms=binding.updated_at + 1000)
            assert manager.hibernate_idle(now_ms=binding.updated_at + 31000)
            assert manager.binding().state == "PAUSED"
            assert manager.execute(["true"])["success"]
            pause.assert_called_once()
            resume.assert_called_once()
            assert manager.binding().state == "RUNNING"
    finally:
        db.close()


def test_operator_mounts_are_readonly_and_cannot_inject_mount_options(tmp_path):
    source = tmp_path / "external"
    source.mkdir()
    driver = ContainerCliRuntimeDriver(RuntimeBackend.DOCKER, "docker", tmp_path)
    with patch("kitt.runtime.conversation_runtime._run_host", return_value={"success": True}) as run:
        driver.provision({"image": "alpine", "readonly_mounts": {"docs": str(source)}})
        argv = run.call_args.args[0]
        assert argv[argv.index("--mount") + 1] == f"type=bind,src={source.resolve()},dst=/mnt/kitt/docs,readonly"
        with pytest.raises(ValueError):
            driver.provision({"image": "alpine", "readonly_mounts": {"../escape": str(source)}})
        injected = tmp_path / "path,readonly=false"
        injected.mkdir()
        with pytest.raises(ValueError):
            driver.provision({"image": "alpine", "readonly_mounts": {"docs": str(injected)}})
        assert run.call_count == 1


def test_reconnect_keeps_original_config_and_paused_state(tmp_path):
    db = HistoryDatabase(tmp_path)
    manager = ConversationRuntimeManager(tmp_path, "ws", "conv", db)
    driver = manager.drivers[RuntimeBackend.DOCKER]
    config = {"image": "alpine", "memory_bytes": 67108864, "hibernate_on_pause": True}
    try:
        with patch.object(driver, "provision", return_value="lab"), patch.object(
            driver, "pause"
        ), patch.object(driver, "health", return_value={"ok": True, "state": "paused"}), patch.object(
            driver, "attach"
        ) as attach:
            manager.provision("DOCKER", config)
            paused = manager.pause()
            restarted = ConversationRuntimeManager(tmp_path, "ws", "conv", db, drivers=manager.drivers)
            assert restarted.attach("DOCKER", "lab", {"image": "alpine"}) == paused
            assert restarted._config() == config
            with pytest.raises(ValueError, match="cannot change"):
                restarted.attach("DOCKER", "lab", {"memory_bytes": 134217728})
            with pytest.raises(RuntimeError, match="different"):
                restarted.attach("DOCKER", "other")
            attach.assert_not_called()
    finally:
        db.close()
