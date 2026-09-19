"""Standalone Landlock launcher. This file intentionally imports no KITT modules."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import platform
import sys


LANDLOCK_RULE_PATH_BENEATH = 1
LANDLOCK_CREATE_RULESET_VERSION = 1
PR_SET_NO_NEW_PRIVS = 38

ACCESS_FS_WRITE_FILE = 1 << 1
ACCESS_FS_REMOVE_DIR = 1 << 4
ACCESS_FS_REMOVE_FILE = 1 << 5
ACCESS_FS_MAKE_CHAR = 1 << 6
ACCESS_FS_MAKE_DIR = 1 << 7
ACCESS_FS_MAKE_REG = 1 << 8
ACCESS_FS_MAKE_SOCK = 1 << 9
ACCESS_FS_MAKE_FIFO = 1 << 10
ACCESS_FS_MAKE_BLOCK = 1 << 11
ACCESS_FS_MAKE_SYM = 1 << 12
ACCESS_FS_REFER = 1 << 13
ACCESS_FS_TRUNCATE = 1 << 14

_SUPPORTED_MACHINES = {"x86_64", "amd64", "aarch64", "arm64", "riscv64"}


class RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class PathBeneathAttr(ctypes.Structure):
    _fields_ = [
        ("allowed_access", ctypes.c_uint64),
        ("parent_fd", ctypes.c_int32),
    ]


def _libc():
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    libc.prctl.restype = ctypes.c_int
    return libc


def _syscall(libc, number: int, *args) -> int:
    ctypes.set_errno(0)
    result = int(libc.syscall(number, *args))
    if result < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return result


def _abi(libc) -> int:
    if sys.platform != "linux" or platform.machine().casefold() not in _SUPPORTED_MACHINES:
        return 0
    try:
        return _syscall(
            libc,
            444,
            ctypes.c_void_p(),
            ctypes.c_size_t(0),
            ctypes.c_uint(LANDLOCK_CREATE_RULESET_VERSION),
        )
    except OSError:
        return 0


def _handled_access(abi: int) -> int:
    rights = (
        ACCESS_FS_WRITE_FILE
        | ACCESS_FS_REMOVE_DIR
        | ACCESS_FS_REMOVE_FILE
        | ACCESS_FS_MAKE_CHAR
        | ACCESS_FS_MAKE_DIR
        | ACCESS_FS_MAKE_REG
        | ACCESS_FS_MAKE_SOCK
        | ACCESS_FS_MAKE_FIFO
        | ACCESS_FS_MAKE_BLOCK
        | ACCESS_FS_MAKE_SYM
    )
    if abi >= 2:
        rights |= ACCESS_FS_REFER
    if abi >= 3:
        rights |= ACCESS_FS_TRUNCATE
    return rights


def _add_path_rule(libc, ruleset_fd: int, path: Path, access: int) -> None:
    flags = int(getattr(os, "O_PATH", os.O_RDONLY)) | int(getattr(os, "O_CLOEXEC", 0))
    path_fd = os.open(str(path), flags)
    try:
        attr = PathBeneathAttr(access, path_fd)
        _syscall(
            libc,
            445,
            ctypes.c_int(ruleset_fd),
            ctypes.c_int(LANDLOCK_RULE_PATH_BENEATH),
            ctypes.byref(attr),
            ctypes.c_uint(0),
        )
    finally:
        os.close(path_fd)


def _restrict(mode: str, root: Path, scratch: Path) -> None:
    libc = _libc()
    abi = _abi(libc)
    if abi <= 0:
        raise RuntimeError("Landlock is unavailable")
    access = _handled_access(abi)
    attr = RulesetAttr(access)
    ruleset_fd = _syscall(
        libc,
        444,
        ctypes.byref(attr),
        ctypes.c_size_t(ctypes.sizeof(attr)),
        ctypes.c_uint(0),
    )
    try:
        _add_path_rule(libc, ruleset_fd, scratch, access)
        if mode == "workspace-write":
            _add_path_rule(libc, ruleset_fd, root, access)

        ctypes.set_errno(0)
        if int(libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))
        _syscall(libc, 446, ctypes.c_int(ruleset_fd), ctypes.c_uint(0))
    finally:
        os.close(ruleset_fd)


def _parse(argv: list[str]) -> tuple[str, Path, Path, Path, list[str]]:
    try:
        separator = argv.index("--")
    except ValueError as exc:
        raise ValueError("sandbox target separator is missing") from exc
    options = argv[:separator]
    target = argv[separator + 1 :]
    if not target:
        raise ValueError("sandbox target argv is empty")
    parsed: dict[str, str] = {}
    index = 0
    while index < len(options):
        key = options[index]
        if key not in {"--mode", "--root", "--scratch", "--cwd"} or index + 1 >= len(options):
            raise ValueError(f"invalid sandbox option: {key}")
        parsed[key] = options[index + 1]
        index += 2
    mode = parsed.get("--mode", "")
    if mode not in {"read-only", "workspace-write"}:
        raise ValueError(f"invalid sandbox mode: {mode}")
    root = Path(parsed["--root"]).resolve(strict=True)
    scratch = Path(parsed["--scratch"]).resolve(strict=True)
    cwd = Path(parsed["--cwd"]).resolve(strict=True)
    cwd.relative_to(root)
    return mode, root, scratch, cwd, target


def main() -> int:
    try:
        mode, root, scratch, cwd, target = _parse(sys.argv[1:])
        _restrict(mode, root, scratch)
        os.chdir(cwd)
        os.execvpe(target[0], target, os.environ)
    except BaseException as exc:
        sys.stderr.write(f"KITT sandbox setup failed: {exc}\n")
        sys.stderr.flush()
        return 126


if __name__ == "__main__":
    raise SystemExit(main())
