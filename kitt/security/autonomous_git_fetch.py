"""Narrow, auditable delegation for public Git source checkout in allow-all mode.

This permits only repository downloads already requested for an autonomous
workspace task. It does not grant general network authority to a model or child.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit


_PUBLIC_GIT_HOSTS = frozenset({"github.com", "gitlab.com", "gitlab.winehq.org"})
_REPOSITORY_PATH = re.compile(r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git")


def may_fetch_public_git_source(
    args: dict,
    *,
    policy: object,
    security_context: object,
    process_runner: object,
    origin: str,
) -> bool:
    """Allow an exact Git HTTPS checkout only for an unscoped root user turn."""
    if origin != "MODEL" or not isinstance(args, dict) or args.get("network") is not True:
        return False
    autonomy = getattr(policy, "autonomy", None)
    if (
        getattr(autonomy, "level", None) != "autonomous"
        or not getattr(autonomy, "allow_run_command_auto", False)
        or getattr(security_context, "principal_type", None) != "USER"
        or getattr(security_context, "principal_id", None) != "user_root"
        or getattr(security_context, "is_path_scoped", True)
    ):
        return False
    argv = args.get("argv")
    if not isinstance(argv, list):
        return False
    if len(argv) == 4 and argv[:2] == ["git", "submodule"] and argv[2] == "add":
        # Never invent a destination: a submodule adds a Git config and directory.
        return False
    if len(argv) == 5 and argv[:3] == ["git", "submodule", "add"]:
        url, target = argv[3], argv[4]
    elif len(argv) == 4 and argv[:2] == ["git", "clone"]:
        url, target = argv[2], argv[3]
    else:
        return False

    if not isinstance(url, str) or not isinstance(target, str):
        return False
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _PUBLIC_GIT_HOSTS
        or parsed.username or parsed.password or parsed.port
        or parsed.query or parsed.fragment
        or not _REPOSITORY_PATH.fullmatch(parsed.path)
    ):
        return False
    dest = Path(target)
    if (
        not target
        or dest.is_absolute()
        or target.startswith("-")
        or any(part in ("", ".", "..") for part in target.replace("\\", "/").split("/"))
        or "\x00" in target
        or ":" in target
    ):
        return False
    try:
        cwd = process_runner.validate_cwd(args.get("cwd"))
        (cwd / dest).resolve().relative_to(process_runner.root)
        sandbox = process_runner.sandbox
        return bool(sandbox.is_strong_available("workspace-write+network"))
    except (ValueError, PermissionError, FileNotFoundError, NotADirectoryError, OSError, AttributeError):
        return False
