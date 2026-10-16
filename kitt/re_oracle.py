"""Fail-closed, opt-in readiness check for the existing Dockur Windows oracle.

A running container proves only Docker state. This check additionally requires
operator-pinned SSH host authentication, a forced-command guest_rpc endpoint,
Windows x64 identity, and the *expected* guest manifest SHA-256.
No probe, guest installer, port binding or Docker lifecycle action is performed.
"""
from __future__ import annotations

from pathlib import Path
import re

from kitt import re_docker_lab
from kitt.extensions.builtin_plugins.reverse_engineering.mission import (
    MissionError, _hosts, probe_guest_info,
)
from kitt.vm_supervisor import VMError

DEFAULT_HOSTS = Path.home() / ".kitt" / "reverse-engineering" / "hosts.json"
HEX64 = re.compile(r"[a-f0-9]{64}\Z")


def doctor(*, manifest_sha256: str, hosts: Path | None = None) -> dict:
    """Operator-invoked, read-only remote attestation *check*, not VM attestation.

    SSH host-key verification authenticates the configured endpoint; receipt
    hashes and the manifest pin do not establish a trusted hypervisor or prove
    that the guest was uncompromised.
    """
    if not isinstance(manifest_sha256, str) or not HEX64.fullmatch(manifest_sha256):
        return {"oracle_ready": False, "status": "BLOCKED_ENV",
                "reason": "Specify the expected 64-character lowercase SHA-256 of the guest manifest"}
    try:
        container = re_docker_lab.inspect("windows")
        if container is None or container.get("status") != "running":
            return {"oracle_ready": False, "status": "BLOCKED_ENV",
                    "reason": "Managed Dockur Windows container must be running"}
        cfg = _hosts(hosts if hosts is not None else DEFAULT_HOSTS)
        identity = probe_guest_info(cfg)
        if identity["manifest_sha256"] != manifest_sha256:
            return {"oracle_ready": False, "status": "BLOCKED_ENV",
                    "reason": "Windows guest probe manifest differs from the operator-pinned SHA-256"}
        return {"oracle_ready": True, "status": "READY", "backend": "docker",
                "container": container["container"],
                "transport": "operator-pinned-ssh-forced-command",
                "guest": identity, "guest_attested": False}
    except (MissionError, VMError, OSError, ValueError) as exc:
        return {"oracle_ready": False, "status": "BLOCKED_ENV",
                "reason": str(exc)[:200]}
