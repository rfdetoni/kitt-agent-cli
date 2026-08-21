from __future__ import annotations

from pathlib import Path

from kitt.daemon.process import start_daemon_detached
from kitt.remote.server import RemoteServer, RemoteServerConfig


def run_remote_command(
    *,
    root_dir: str,
    lan: bool = False,
    host: str | None = None,
    port: int = 7337,
    pairing_ttl: float = 900.0,
    session_ttl: float = 43_200.0,
    tls_cert: str | None = None,
    tls_key: str | None = None,
) -> int:
    root = str(Path(root_dir).expanduser().resolve())
    bind_host = str(host or ("0.0.0.0" if lan else "127.0.0.1"))
    if not lan and bind_host not in {"127.0.0.1", "::1", "localhost"}:
        print("\033[31mNon-loopback bind requires --lan.\033[0m")
        return 1

    # Validate network configuration before producing the side effect of
    # starting the persistent daemon. RemoteServerConfig also validates it.
    try:
        config = RemoteServerConfig(
            workspace_root=root,
            host=bind_host,
            port=port,
            pairing_ttl_seconds=pairing_ttl,
            session_ttl_seconds=session_ttl,
            tls_cert=tls_cert,
            tls_key=tls_key,
        ).validated()
    except Exception as exc:
        print(f"\033[31mInvalid KITT Remote configuration: {exc}\033[0m")
        return 1

    daemon = start_daemon_detached(root)
    if daemon.get("status") != "ok":
        print(f"\033[31mUnable to start KITT daemon: {daemon.get('error')}\033[0m")
        return 1

    try:
        server = RemoteServer(config)
        server.start()
    except Exception as exc:
        print(f"\033[31mUnable to start KITT Remote: {exc}\033[0m")
        return 1

    print("\n\033[1;31mK.I.T.T. Remote Control\033[0m")
    print(f"Workspace: {root}")
    for url in server.display_urls():
        print(f"URL: \033[1m{url}\033[0m")
    print(f"Pairing code: \033[1;33m{server.auth.pairing_code}\033[0m")
    print("The pairing code expires automatically; remote sessions live only in memory.")
    if not server.uses_tls:
        print("\033[33mLAN HTTP is intended for trusted private networks. Use --tls-cert/--tls-key on untrusted Wi-Fi.\033[0m")
    print("Press Ctrl+C to stop the web gateway. The persistent KITT daemon is left running.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return 0
