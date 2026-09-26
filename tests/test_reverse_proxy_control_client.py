import urllib.error

from kitt.reverse_proxy.client import ReverseProxyClient


def test_control_client_prefers_resident_channel(monkeypatch):
    client = ReverseProxyClient(executable="kitt-reverse-proxy")
    calls = []

    def http_call(action, params):
        calls.append((action, params))
        return {"schema_version": 1, "instances": []}

    monkeypatch.setattr(client, "_http_call", http_call)
    monkeypatch.setattr(
        client,
        "_call",
        lambda *args: (_ for _ in ()).throw(AssertionError(f"unexpected fallback: {args}")),
    )

    assert client.list_instances() == []
    assert calls == [("service.list", {})]


def test_control_client_bootstraps_then_retries_http(monkeypatch):
    client = ReverseProxyClient(executable="kitt-reverse-proxy")
    attempts = {"http": 0, "bootstrap": 0}

    def http_call(action, params):
        attempts["http"] += 1
        if attempts["http"] == 1:
            raise urllib.error.URLError("offline")
        return {"schema_version": 1, "profiles": []}

    monkeypatch.setattr(client, "_http_call", http_call)
    monkeypatch.setattr(
        client,
        "_bootstrap_control_plane",
        lambda: attempts.__setitem__("bootstrap", attempts["bootstrap"] + 1),
    )

    assert client.list_profiles() == []
    assert attempts == {"http": 2, "bootstrap": 1}


def test_control_client_falls_back_to_cli(monkeypatch):
    client = ReverseProxyClient(executable="kitt-reverse-proxy")
    monkeypatch.setattr(
        client,
        "_http_call",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(urllib.error.URLError("offline")),
    )
    monkeypatch.setattr(client, "_bootstrap_control_plane", lambda: None)
    monkeypatch.setattr(
        client,
        "_call",
        lambda *args: {"schema_version": 1, "plugins": [], "fallback": list(args)},
    )

    assert client.list_plugins() == []
