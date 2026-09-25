"""Compatibility commands backed by the reverse-proxy control-plane client."""
from __future__ import annotations

from kitt.reverse_proxy.client import ReverseProxyClient, ReverseProxyControlError


def manage_reverse_proxy(restart: bool) -> str:
    client = ReverseProxyClient()
    try:
        instances = client.list_instances()
        if not instances:
            return "Nenhum serviço do KITT Reverse Proxy está ativo."
        if not restart:
            stopped = client.stop_all()
            return f"{stopped} serviço(s) do KITT Reverse Proxy parado(s)."
        restarted = [client.restart_instance(instance.id) for instance in instances]
        return f"{len(restarted)} serviço(s) do KITT Reverse Proxy reiniciado(s)."
    except ReverseProxyControlError as exc:
        raise RuntimeError(str(exc)) from exc
