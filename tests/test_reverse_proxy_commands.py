import subprocess
from unittest.mock import patch

from kitt.ui.reverse_proxy_commands import ProxyProcess, manage_reverse_proxy


def test_stop_all_reverse_proxy_services():
    processes = [ProxyProcess(10, ("node", "/bin/kitt-reverse-proxy", "start", "gemini"), "/tmp")]
    with patch("kitt.ui.reverse_proxy_commands._instances", return_value=processes), patch(
        "kitt.ui.reverse_proxy_commands._stop"
    ) as stop:
        assert manage_reverse_proxy(False) == "1 serviço(s) do KITT Reverse Proxy parado(s)."
    stop.assert_called_once_with(processes)


def test_restart_preserves_each_service_arguments():
    processes = [ProxyProcess(10, ("node", "/bin/kitt-reverse-proxy", "start", "gemini", "--port", "3001"), "/tmp")]
    with patch("kitt.ui.reverse_proxy_commands._instances", return_value=processes), patch(
        "kitt.ui.reverse_proxy_commands._stop"
    ), patch("kitt.ui.reverse_proxy_commands.shutil.which", return_value="/installed/kitt-reverse-proxy"), patch(
        "kitt.ui.reverse_proxy_commands.subprocess.Popen"
    ) as popen:
        assert manage_reverse_proxy(True) == "1 serviço(s) do KITT Reverse Proxy reiniciado(s)."
    popen.assert_called_once_with(
        ["/installed/kitt-reverse-proxy", "start", "gemini", "--port", "3001"],
        cwd="/tmp",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
