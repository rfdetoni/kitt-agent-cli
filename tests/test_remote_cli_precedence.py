from __future__ import annotations

from unittest.mock import patch
from kitt.remote.cli import run_remote_command


def test_remote_cli_lan_protection() -> None:
    # Non-loopback host without lan flag must fail closed
    res = run_remote_command(root_dir=".", lan=False, host="0.0.0.0")
    assert res == 1


def test_remote_cli_control_center_precedence() -> None:
    fake_section = {
        "host": "127.0.0.1",
        "port": 9999,
        "pairing_ttl_seconds": 300.0,
        "session_ttl_seconds": 7200.0,
        "tls_cert": None,
        "tls_key": None,
    }
    with patch("kitt.remote.cli.control_center_section", return_value=fake_section), \
         patch("kitt.remote.cli.start_daemon_detached", return_value={"status": "error", "error": "mocked stop"}):
        # When CLI args are None, Control Center defaults are used
        res = run_remote_command(root_dir=".", host=None, port=None)
        assert res == 1

        # Explicit CLI override takes precedence over Control Center
        with patch("kitt.remote.cli.RemoteServerConfig") as mock_cfg:
            run_remote_command(root_dir=".", host="127.0.0.1", port=8888)
            assert mock_cfg.call_args[1]["port"] == 8888
