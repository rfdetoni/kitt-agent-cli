from unittest.mock import patch

from kitt.reverse_proxy.contracts import ReverseProxyInstance
from kitt.ui.reverse_proxy_commands import manage_reverse_proxy


def _instance(instance_id: str, port: int) -> ReverseProxyInstance:
    return ReverseProxyInstance(
        id=instance_id,
        provider="gemini",
        model="gemini-web",
        target="gemini",
        profile_id="gemini-default",
        host="127.0.0.1",
        port=port,
        pid=1000 + port,
        status="ready",
        started_at="2026-09-25T00:00:00Z",
    )


def test_stop_all_reverse_proxy_services_uses_control_plane():
    client = patch("kitt.ui.reverse_proxy_commands.ReverseProxyClient").start()
    try:
        client.return_value.list_instances.return_value = [_instance("gemini-context", 3000)]
        client.return_value.stop_all.return_value = 1

        assert manage_reverse_proxy(False) == "1 serviço(s) do KITT Reverse Proxy parado(s)."

        client.return_value.stop_all.assert_called_once_with()
    finally:
        patch.stopall()


def test_restart_preserves_control_plane_instance_identity():
    with patch("kitt.ui.reverse_proxy_commands.ReverseProxyClient") as client:
        original = _instance("gemini-context", 3000)
        restarted = _instance("gemini-context", 3000)
        client.return_value.list_instances.return_value = [original]
        client.return_value.restart_instance.return_value = restarted

        assert manage_reverse_proxy(True) == "1 serviço(s) do KITT Reverse Proxy reiniciado(s)."

        client.return_value.restart_instance.assert_called_once_with("gemini-context")
