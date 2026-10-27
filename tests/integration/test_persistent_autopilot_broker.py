"""Real Docker smoke for broker persistence, resource boundaries and recovery."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from kitt.security.autopilot_broker import DockerBroker


@pytest.mark.skipif(
    os.environ.get("KITT_CONTAINER_RUNTIME") != "docker",
    reason="Docker integration job only",
)
def test_broker_worker_reuse_isolation_and_reprovision(tmp_path: Path, monkeypatch):
    if not shutil.which("docker"):
        pytest.skip("docker CLI unavailable")
    root = tmp_path / "workspace"
    root.mkdir()
    (root / ".kitt").mkdir()
    (root / ".kitt" / "secret").write_text("CONTROL_PLANE")
    monkeypatch.setenv("KITT_BROKER_DEFAULT_IMAGE", "alpine:3.20")
    monkeypatch.delenv("KITT_BROKER_ALLOW_NETWORK", raising=False)
    broker = DockerBroker(str(root), broker_root=str(root))
    worker = broker._worker_name("alpine:3.20", "workspace-write")
    readonly = broker._worker_name("alpine:3.20", "read-only")
    try:
        first = broker.execute({
            "profile": "workspace-write",
            "argv": ["sh", "-c", "mkdir -p /home/kitt/cache; printf reusable > /home/kitt/cache/item"],
        })
        assert first["returncode"] == 0, first["stderr"]
        second = broker.execute({
            "profile": "workspace-write",
            "argv": ["sh", "-c", "test \\"$(cat /home/kitt/cache/item)\\" = reusable "
                     "&& test ! -e .kitt/secret && test ! -e /var/run/docker.sock "
                     "&& printf passed > proof"],
        })
        assert second["returncode"] == 0, second["stderr"]
        assert (root / "proof").read_text() == "passed"
        forbidden = broker.execute({
            "profile": "read-only", "argv": ["sh", "-c", "touch forbidden"],
        })
        assert forbidden["returncode"] != 0
        assert not (root / "forbidden").exists()
        with pytest.raises(PermissionError, match="network"):
            broker.execute({
                "profile": "workspace-write+network",
                "argv": ["sh", "-c", "true"],
            })
        broker._call(["rm", "-f", worker])
        recovered = broker.execute({
            "profile": "workspace-write",
            "argv": ["sh", "-c", "printf restarted > after-recovery"],
        })
        assert recovered["returncode"] == 0, recovered["stderr"]
        assert (root / "after-recovery").read_text() == "restarted"
        assert (root / ".kitt" / "secret").read_text() == "CONTROL_PLANE"
    finally:
        broker._call(["rm", "-f", worker])
        broker._call(["rm", "-f", readonly])
