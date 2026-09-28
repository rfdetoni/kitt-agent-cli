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


def test_control_client_uses_startup_timeout_for_service_start(monkeypatch):
    client = ReverseProxyClient(
        executable="kitt-reverse-proxy",
        timeout_seconds=15.0,
        startup_timeout_seconds=42.0,
    )
    observed = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"schema_version":1,"instance":{"id":"gemini-3000","provider":"gemini","model":"gemini-web","endpoint":"http://127.0.0.1:3000","profile_id":"default","status":"ready"}}'

    def urlopen(_request, timeout):
        observed["timeout"] = timeout
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)

    client._http_call("service.start", {"target": "gemini"})
    assert observed["timeout"] == 42.0


def test_control_cli_fallback_uses_startup_timeout(monkeypatch):
    client = ReverseProxyClient(
        executable="kitt-reverse-proxy",
        timeout_seconds=15.0,
        startup_timeout_seconds=42.0,
    )
    observed = {}

    def run(command, **kwargs):
        observed["command"] = command
        observed["timeout"] = kwargs["timeout"]
        return type(
            "Completed",
            (),
            {
                "returncode": 0,
                "stdout": '{"schema_version":1,"instance":{"id":"gemini-3000"}}\n',
                "stderr": "",
            },
        )()

    monkeypatch.setattr("subprocess.run", run)

    client._call("service", "start", "gemini")
    assert observed["timeout"] == 42.0
