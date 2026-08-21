# -*- coding: utf-8 -*-
"""HTTP/Web Remote Command Handler for K.I.T.T. Terminal UI."""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from kitt.daemon.process import start_daemon_detached
from kitt.remote.server import RemoteServer, RemoteServerConfig

if TYPE_CHECKING:
    from kitt.ui.app import KittUIApp


async def handle_remote_command(app: KittUIApp, arg: str) -> None:
    """Handle /remote slash command to control the HTTP/web gateway."""
    tokens = arg.strip().split()
    subcommand = tokens[0].lower() if tokens else "toggle"

    if subcommand in {"help", "-h", "--help"}:
        app._show_result(
            "=== K.I.T.T. REMOTE / WEB GATEWAY ===\n\n"
            "Permite acessar o agente pelo navegador neste computador ou em outros dispositivos da rede.\n\n"
            "Uso:\n"
            "  /remote                Inicia ou exibe status do servidor web\n"
            "  /remote start [port]   Inicia o gateway web (padrao: 7337)\n"
            "  /remote lan [port]     Inicia com acesso liberado na rede local (LAN)\n"
            "  /remote status         Exibe status, URLs ativas e codigo de pareamento\n"
            "  /remote code           Gera um novo codigo de pareamento (PIN)\n"
            "  /remote stop           Encerra o servidor web\n"
        )
        return

    server: RemoteServer | None = getattr(app, "_remote_server", None)

    if subcommand == "stop":
        if server is None:
            app._show_result("K.I.T.T. Web Remote nao esta em execucao.")
            return
        try:
            server.stop()
        except Exception as exc:
            app._show_result(f"Erro ao parar K.I.T.T. Web Remote: {exc}")
            return
        finally:
            app._remote_server = None
        app.state.add_toast("Web Remote desativado.")
        app._show_result("K.I.T.T. Web Remote encerrado com sucesso.")
        return

    if subcommand == "status":
        if server is None:
            app._show_result(
                "K.I.T.T. Web Remote esta DESATIVADO.\n"
                "Use /remote ou /remote start para ativar."
            )
            return
        _show_server_info(app, server)
        return

    if subcommand in {"code", "rotate", "refresh"}:
        if server is None:
            app._show_result(
                "K.I.T.T. Web Remote nao esta em execucao.\n"
                "Inicie com /remote para gerar um codigo."
            )
            return
        new_code = server.auth.rotate_pairing_code()
        app.state.add_toast(f"Novo PIN Remote: {new_code}")
        app._show_result(
            f"Novo Codigo de Pareamento gerado: {new_code}\n"
            "Valido por 15 minutos. Insira este codigo no navegador para conectar."
        )
        return

    if subcommand in {"toggle", "show", "info"} and server is not None:
        _show_server_info(app, server)
        return

    lan = False
    port = 7337

    for tok in tokens:
        t_low = tok.lower()
        if t_low in {"lan", "--lan", "-l"}:
            lan = True
        elif t_low.isdigit():
            port = int(t_low)
        elif t_low.startswith("--port="):
            try:
                port = int(t_low.split("=", 1)[1])
            except ValueError:
                pass

    if server is not None:
        try:
            server.stop()
        except Exception:
            pass
        app._remote_server = None

    workspace_root = str(getattr(app.state, "workspace_path", "") or app.runtime.canonical_root)
    bind_host = "0.0.0.0" if lan else "127.0.0.1"

    daemon_res = start_daemon_detached(workspace_root)
    if daemon_res.get("status") != "ok":
        app._show_result(f"Falha ao iniciar daemon K.I.T.T.: {daemon_res.get(error)}")
        return

    try:
        config = RemoteServerConfig(
            workspace_root=workspace_root,
            host=bind_host,
            port=port,
        ).validated()
        new_server = RemoteServer(config)
        new_server.start()
    except Exception as exc:
        app._show_result(f"Falha ao iniciar K.I.T.T. Web Remote: {exc}")
        return

    worker = threading.Thread(
        target=new_server.serve_forever,
        daemon=True,
        name="kitt-ui-remote-server",
    )
    worker.start()
    app._remote_server = new_server

    urls = new_server.display_urls()
    primary_url = urls[0] if urls else f"http://{bind_host}:{port}"
    app.state.add_toast(f"Web Remote ativo: {primary_url}")
    _show_server_info(app, new_server, newly_started=True)


def _show_server_info(app: KittUIApp, server: RemoteServer, newly_started: bool = False) -> None:
    urls = server.display_urls()
    host, port = server.address
    is_lan = host in {"0.0.0.0", "::"}
    pin = server.auth.pairing_code

    header = "K.I.T.T. WEB REMOTE CONTROL ATIVADO" if newly_started else "K.I.T.T. WEB REMOTE STATUS"
    access_desc = "LAN Privada (Outros dispositivos na rede)" if is_lan else "Local (Apenas este dispositivo)"
    lines = [
        f"=== {header} ===\n",
        f"Status:  ONLINE (Porta: {port})",
        f"Acesso:  {access_desc}\n",
        "URLs para acessar no navegador:",
    ]
    for url in urls:
        lines.append(f"  -> {url}")

    lines.extend([
        f"\nCodigo de Pareamento (PIN): {pin}",
        "(Digite este PIN de 8 digitos no navegador para autenticar a sessao)\n",
        "Comandos rapidos:",
        "  /remote code   -> Gerar novo codigo de pareamento",
        "  /remote stop   -> Parar o servidor web",
        "  /remote lan    -> Reiniciar com acesso liberado na rede local",
    ])
    app._show_result("\n".join(lines))
