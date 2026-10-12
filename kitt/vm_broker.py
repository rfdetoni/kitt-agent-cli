"""Operator-owned constrained multi-guest probe broker.

Routes named, hash-pinned probes through existing forced-command SSH guest
endpoints and validates evidence via the reverse-engineering mission contract.
No Docker socket, model-defined shell, or guest-to-guest network permissions.
"""
from __future__ import annotations

from pathlib import Path
from .vm_supervisor import VMError


def run_lab_mission(*, workspace: Path, mission: Path, hosts: Path) -> dict:
    try:
        from kitt.extensions.builtin_plugins.reverse_engineering.mission import (
            MissionError, run_mission,
        )
        result = run_mission(workspace, mission, hosts)
    except (MissionError, OSError, ValueError) as error:
        raise VMError(f"Lab broker blocked mission: {str(error)[:180]}") from error
    if not isinstance(result, dict):
        raise VMError("Lab broker returned an invalid report")
    return result


def doctor(hosts: Path) -> dict:
    try:
        from kitt.extensions.builtin_plugins.reverse_engineering.mission import _hosts, MissionError
        profile = _hosts(hosts)
    except (MissionError, OSError, ValueError) as error:
        raise VMError(f"Guest hosts profile invalid: {str(error)[:180]}") from error
    return {"ready": True, "reference": profile["reference"],
            "candidate": profile["candidate"],
            "transport": "operator-pinned-ssh-forced-command",
            "guest_network_attested": False}
