"""Bounded SSH diagnostics and read-only Oracle recovery."""
from __future__ import annotations

import pytest

from kitt import re_oracle
from kitt.extensions.builtin_plugins.reverse_engineering import mission


@pytest.mark.parametrize(("message", "code"), [
    (b"ssh: connect to host 127.0.0.1 port 2222: Connection refused", "SSH_UNAVAILABLE"),
    (b"ssh: connect to host 127.0.0.1 port 2222: Connection timed out", "SSH_TIMEOUT"),
    (b"Connection closed by 127.0.0.1", "SSH_DISCONNECTED"),
    (b"Permission denied (publickey).", "SSH_AUTH_FAILED"),
    (b"Host key verification failed.", "SSH_HOST_KEY_REJECTED"),
    (b"unexpected ssh error /private/key/path", "SSH_FAILED"),
])
def test_ssh_error_categories_are_bounded(message, code):
    result = mission._ssh_failure_code(message)
    assert result == code
    assert "/private/key/path" not in result


def _identity():
    return {"version": 1, "guest": {"platform": "Windows", "release": "11",
            "architecture": "AMD64", "manifest_sha256": "a" * 64, "probe_count": 1}}


def test_probe_info_retries_one_transient_failure(monkeypatch):
    calls = []
    def exchange(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            raise mission.MissionError("Guest oracle: SSH_UNAVAILABLE (ssh exit 255)")
        return _identity()
    monkeypatch.setattr(mission, "_ssh_exchange", exchange)
    result = mission.probe_guest_info({"reference": "oracle", "candidate": "wine"})
    assert result["platform"] == "Windows"
    assert len(calls) == 2
    assert all(call[2]["method"] == "probe.info" for call in calls)


def test_probe_info_does_not_retry_authentication_failure(monkeypatch):
    calls = []
    def exchange(*args, **kwargs):
        calls.append(args)
        raise mission.MissionError("Guest oracle: SSH_AUTH_FAILED (ssh exit 255)")
    monkeypatch.setattr(mission, "_ssh_exchange", exchange)
    with pytest.raises(mission.MissionError, match="SSH_AUTH_FAILED"):
        mission.probe_guest_info({"reference": "oracle", "candidate": "wine"})
    assert len(calls) == 1


def test_oracle_doctor_reports_ssh_phase_without_readiness(monkeypatch):
    monkeypatch.setattr(re_oracle.re_docker_lab, "inspect",
                        lambda _: {"status": "running", "container": "kitt-re-win11"})
    monkeypatch.setattr(re_oracle, "_hosts", lambda _: {"reference": "oracle", "candidate": "wine"})
    def fail(_):
        raise mission.MissionError("Guest oracle: SSH_UNAVAILABLE (ssh exit 255)")
    monkeypatch.setattr(re_oracle, "probe_guest_info", fail)
    result = re_oracle.doctor(manifest_sha256="a" * 64)
    assert result["status"] == "SSH_UNAVAILABLE"
    assert result["oracle_ready"] is False
