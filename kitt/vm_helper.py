"""Interactive entry point for creating operator-owned Windows or Linux VMs.

Uses the existing, isolated QEMU setup wizards. It never downloads a guest OS,
runs the user-supplied image, or changes the normal KITT coding-agent path.
"""
from __future__ import annotations

from pathlib import Path
import sys

from .vm_supervisor import VMError

SYSTEMS = {"1": "windows", "2": "linux", "windows": "windows", "linux": "linux"}


def create_vm(
    *, system: str | None = None, source: Path | None = None,
    kind: str | None = None, destination: Path | None = None,
    memory_mib: int = 4096, cpus: int = 2, disk_gib: int = 64,
    variant: str | None = None, distro: str | None = None,
    tools: str | None = None, yes: bool = False,
    backend: str | None = None, port: int = 18006,
) -> tuple[str, Path]:
    """Ask which guest to create, request the media path, delegate safely."""
    if system is None:
        if yes or not sys.stdin.isatty():
            raise VMError("Choose --os windows or --os linux in noninteractive mode")
        print("Criar máquina virtual KITT")
        print("  1. Windows — ISO ou QCOW2 fornecido pelo usuário")
        print("  2. Linux — ISO ou QCOW2 fornecido pelo usuário")
        answer = input("Escolha o sistema [1/2]: ").strip().lower()
        system = SYSTEMS.get(answer)
        if system is None:
            raise VMError("Selecione 1 (Windows) ou 2 (Linux)")

    if system not in ("windows", "linux"):
        raise VMError("Sistema inválido; escolha Windows ou Linux")
    if system == "windows" and (distro is not None or tools is not None):
        raise VMError("Os parâmetros --distro e --tools são exclusivos do Linux")
    if system == "linux" and variant is not None:
        raise VMError("O parâmetro --variant é exclusivo do Windows")

    if source is None:
        if yes or not sys.stdin.isatty():
            raise VMError("Informe --source com o caminho da ISO ou QCOW2")
        entered = input(f"Caminho completo da ISO ou QCOW2 para {system.capitalize()}: ").strip()
        if not entered:
            raise VMError("O caminho da imagem é obrigatório")
        source = Path(entered)

    path = source.expanduser().absolute()
    # Delegate full security and hypervisor checks to the existing wizards.
    # Checking the file here prevents prompting for settings when it is absent.
    if path.is_symlink() or not path.is_file():
        raise VMError("A ISO/QCOW2 deve existir e ser um arquivo regular, sem symlink")
    inferred = {".iso": "iso", ".qcow2": "qcow2"}.get(path.suffix.lower())
    if inferred is None:
        raise VMError("Formato inválido: selecione um arquivo .iso ou .qcow2")
    if kind is not None and kind != inferred:
        raise VMError("--kind não corresponde à extensão do arquivo fornecido")
    selected_kind = kind or inferred

    if backend is None:
        if not yes and sys.stdin.isatty():
            answer = input("Backend (native/docker/podman) [native]: ").strip().lower()
            backend = answer or "native"
        else:
            backend = "native"
    if backend not in ("native", "docker", "podman"):
        raise VMError("Backend deve ser native, docker ou podman")
    if backend != "native" and (tools is not None or distro is not None):
        raise VMError(
            "--distro/--tools apply to native Linux guests only; use "
            "'kitt vm container toolbox' for prebuilt reverse-engineering tools"
        )
    if backend != "native":
        from .vm_container import prepare_vm
        default_dest = Path.home() / ".local/share/kitt/vm-containers" / f"{system}-{backend}"
        if not yes:
            if not sys.stdin.isatty():
                raise VMError("Use --yes em modo não interativo")
            print(f"Backend {backend}; sistema {system}; imagem {path}")
            if input("Criar VM em contêiner? [s/N] ").strip().lower() not in ("s", "sim", "y", "yes"):
                raise VMError("Operação cancelada")
        result = prepare_vm(
            runtime=backend, system=system, source=path,
            kind=selected_kind, destination=destination or default_dest,
            memory_mib=memory_mib, cpus=cpus, disk_gib=disk_gib, port=port,
        )
        return system, result
    if system == "windows":
        from .vm_windows import wizard

        result = wizard(
            source=path, kind=selected_kind, variant=variant,
            destination=destination, memory_mib=memory_mib,
            cpus=cpus, disk_gib=disk_gib, yes=yes,
        )
    else:
        from .vm_linux import wizard

        result = wizard(
            source=path, kind=selected_kind, distro=distro, tools=tools,
            destination=destination, memory_mib=memory_mib,
            cpus=cpus, disk_gib=disk_gib, yes=yes,
        )
    return system, result
