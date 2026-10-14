"""Reverse-engineering environment manager and guided setup in the existing TUI."""
from __future__ import annotations

import asyncio
from pathlib import Path

from kitt import re_environment


SYSTEMS = ("windows", "linux", "toolbox", "rea")


async def refresh(ui) -> None:
    try:
        ui.re_snapshot = await ui._run_blocking(re_environment.status)
        ui.re_error = ""
    except Exception as exc:
        ui.re_error = str(exc)
    if ui.application:
        ui.application.invalidate()


async def open_panel(ui) -> None:
    ui.re_selected = getattr(ui, "re_selected", "windows")
    ui.open_overlay("re_panel", ui.re_panel_control)
    await refresh(ui)


def panel_text(ui) -> str:
    snapshot = getattr(ui, "re_snapshot", {})
    selected = getattr(ui, "re_selected", "windows")
    guests = snapshot.get("guests", {})
    lines = [
        "RE / LAB — ambientes sob demanda",
        "",
        "Nada e iniciado ao abrir o KITT. Inicio automatico exige perfil inscrito e opt-in.",
        "",
    ]
    ui.interactions.begin("re_panel")
    for system in SYSTEMS:
        if system in guests:
            g = guests[system]
            status = g.get("status", "unverified")
            mode = " [AUTO]" if g.get("auto") else ""
        elif system == "rea":
            status = "installed" if snapshot.get("rea", {}).get("installed") else "missing"
            mode = ""
        else:
            status = "ephemeral"
            mode = ""
        mark = ">" if selected == system else " "
        lines.append(f" {mark} {system.upper():<8} {status}{mode}")
        ui.interactions.add_row("re_panel", len(lines)-1, "re.select", system)
    lines += [
        "",
        f"Selecionado: {selected.upper()}",
        " [s] Iniciar  [x] Parar  [p] Preparar / Helper",
        " [e] Registrar VM existente  [a] Alternar auto  [b] Build imagem  [F5] Atualizar",
        " [Esc] Fechar   (Setas e Enter tambem funcionam)",
    ]
    action_rows = [(len(lines)-3, [
        ("[s] Iniciar", "start"), ("[x] Parar", "stop"),
        ("[p] Preparar / Helper", "setup"),
    ]), (len(lines)-2, [
        ("[e] Registrar VM existente", "enroll"),
        ("[a] Alternar auto", "auto"),
        ("[b] Build imagem", "build"), ("[F5] Atualizar", "refresh"),
    ])]
    for index, buttons in action_rows:
        for text, action in buttons:
            ui.interactions.add_text("re_panel", index, lines[index], text, "re.action", action)
    if ui.re_error:
        lines.extend(["", "ERRO: " + ui.re_error[:500]])
    elif selected in guests:
        item = guests[selected]
        if item.get("reason"):
            lines.extend(["", "Detalhe: " + item["reason"][:500]])
    return "\n".join(lines)


def select(ui, delta: int) -> None:
    current = SYSTEMS.index(getattr(ui, "re_selected", "windows"))
    ui.re_selected = SYSTEMS[(current + delta) % len(SYSTEMS)]
    if ui.application:
        ui.application.invalidate()


def setup_help_text(ui) -> str:
    return (
        f"Preparar {ui.re_selected.upper()} | Backend: {ui.re_backend} (Tab para alternar)\n"
        "Digite o caminho absoluto de uma ISO ou QCOW2 que voce possui e pressione Enter.\n"
        "Esc cancela. Nenhum SO e baixado; a instalacao do convidado e manual."
    )


def open_setup(ui) -> None:
    system = getattr(ui, "re_selected", "windows")
    if system not in ("windows", "linux"):
        asyncio.create_task(action(ui, "setup"))
        return
    ui.re_setup_source_buffer.reset()
    ui.open_overlay("re_setup", ui.re_setup_source_control, parent_name="re_panel")


async def submit_setup(ui, buffer) -> None:
    source = buffer.text.strip()
    if not source:
        ui.re_error = "Escolha o caminho de uma ISO ou QCOW2 existente"
        return
    system = ui.re_selected
    try:
        result = await ui._run_blocking(
            re_environment.setup, system, runtime=ui.re_backend, source=Path(source)
        )
    except Exception as exc:
        ui.re_error = str(exc)
        ui.state.add_toast("Falha ao preparar RE: " + str(exc)[:140], persistent=True)
        return
    ui.close_overlay()
    ui.close_overlay()
    ui._show_result("RE helper:\n" + "\n".join(f"{k}: {v}" for k, v in result.items()))
    await refresh(ui)


async def action(ui, operation: str) -> None:
    from kitt import vm_container
    system = getattr(ui, "re_selected", "windows")
    if operation == "refresh":
        await refresh(ui)
        return
    if operation == "setup" and system in ("windows", "linux"):
        open_setup(ui)
        return
    try:
        if operation == "build":
            result = await ui._run_blocking(
                vm_container.build_image, runtime=ui.re_backend,
                image="toolbox" if system == "toolbox" else "qemu", profile="full",
            )
        elif operation == "setup":
            result = await ui._run_blocking(re_environment.setup, system, runtime=ui.re_backend)
        elif operation == "enroll":
            if system not in ("windows", "linux"):
                raise ValueError("Somente VMs Windows/Linux requerem registro")
            profile = Path.home() / ".local/share/kitt/vm-containers" / f"{system}-{ui.re_backend}" / "profile.json"
            result = await ui._run_blocking(re_environment.enroll, system, profile)
        elif operation == "auto":
            current = ui.re_snapshot.get("guests", {}).get(system, {})
            result = await ui._run_blocking(re_environment.set_auto, system, not current.get("auto", False))
        elif operation == "start":
            if system not in ("windows", "linux"):
                result = await ui._run_blocking(re_environment.setup, system, runtime=ui.re_backend)
            else:
                result = await ui._run_blocking(re_environment.ensure, system)
        elif operation == "stop":
            if system not in ("windows", "linux"):
                raise ValueError("Nao existe VM persistente para parar neste perfil")
            result = await ui._run_blocking(re_environment.stop, system)
        else:
            return
        ui.re_error = ""
        ui._show_result("RE: " + "\n".join(f"{k}: {v}" for k, v in result.items()))
    except Exception as exc:
        ui.re_error = str(exc)
        ui.state.add_toast(f"RE: {exc}", persistent=True)
    await refresh(ui)


async def command(ui, argument: str) -> None:
    parts = argument.split()
    if not parts:
        await open_panel(ui)
        return
    op = parts[0].lower()
    if op not in ("status", "setup", "start", "stop", "enroll", "auto"):
        ui._show_result("Uso: /re [status|setup|start|stop|enroll|auto] [windows|linux|toolbox|rea]")
        return
    if len(parts) > 1 and parts[1] in SYSTEMS:
        ui.re_selected = parts[1]
    if op == "status":
        await refresh(ui)
        ui._show_result(str(ui.re_snapshot))
    else:
        await action(ui, op)
