"""Lifecycle controls for locally owned KITT reverse-proxy processes."""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProxyProcess:
    pid: int
    arguments: tuple[str, ...]
    cwd: str | None


def _instances() -> list[ProxyProcess]:
    proc = Path("/proc")
    if os.name != "posix" or not proc.is_dir():
        raise RuntimeError("controle do reverse proxy requer Linux")

    found = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            arguments = tuple(
                part.decode("utf-8", "surrogateescape")
                for part in (entry / "cmdline").read_bytes().split(b"\0")
                if part
            )
            if not any(Path(part).name == "kitt-reverse-proxy" for part in arguments):
                continue
            cwd = os.readlink(entry / "cwd")
            found.append(ProxyProcess(int(entry.name), arguments, cwd))
        except OSError:
            continue
    return found


def _stop(instances: list[ProxyProcess], timeout: float = 5.0) -> None:
    for process in instances:
        try:
            os.kill(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    deadline = time.monotonic() + timeout
    remaining = {process.pid for process in instances}
    while remaining and time.monotonic() < deadline:
        remaining = {pid for pid in remaining if Path(f"/proc/{pid}").exists()}
        if remaining:
            time.sleep(0.05)
    for pid in remaining:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def manage_reverse_proxy(restart: bool) -> str:
    instances = _instances()
    if not instances:
        return "Nenhum serviço do KITT Reverse Proxy está ativo."

    launches: list[tuple[list[str], str | None]] = []
    if restart:
        executable = shutil.which("kitt-reverse-proxy")
        if not executable:
            raise RuntimeError("executável kitt-reverse-proxy não encontrado no PATH")
        for process in instances:
            marker = next(
                index for index, part in enumerate(process.arguments)
                if Path(part).name == "kitt-reverse-proxy"
            )
            launches.append(([executable, *process.arguments[marker + 1 :]], process.cwd))

    _stop(instances)
    if not restart:
        return f"{len(instances)} serviço(s) do KITT Reverse Proxy parado(s)."

    for arguments, cwd in launches:
        subprocess.Popen(
            arguments,
            cwd=cwd if cwd and Path(cwd).is_dir() else None,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    return f"{len(launches)} serviço(s) do KITT Reverse Proxy reiniciado(s)."
