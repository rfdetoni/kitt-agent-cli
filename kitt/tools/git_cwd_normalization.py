"""Safely map a common Git working-directory flag onto the process cwd.

Git -C is intentionally denied by PolicyEngine because it can bypass the
workspace policy. Only translate its leading, single, relative directory form
after ProcessRunner verifies that the directory belongs to the workspace.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def normalize_git_cwd_args(args: dict[str, Any], process_runner: Any) -> dict[str, Any]:
    argv = args.get("argv")
    if not isinstance(argv, list) or len(argv) < 2 or argv[:2] != ["git", "-C"]:
        return args
    if len(argv) < 4 or not isinstance(argv[2], str):
        raise ValueError("git -C requires a workspace-relative directory and subcommand")
    target = argv[2]
    parts = target.replace("\\", "/").split("/")
    if (
        not target or target.startswith("-") or Path(target).is_absolute()
        or any(part in {"", ".", "..", ".git", ".kitt"} for part in parts)
        or "\x00" in target or ":" in target
        or args.get("cwd") is not None
    ):
        raise ValueError("git -C accepts only one safe workspace-relative directory")
    # Resolve (including symlinks) against the *actual* sandbox workspace.
    # Preserve existing deny rules for all remaining Git flags.
    resolved = process_runner.validate_cwd(target)
    safe = resolved.relative_to(process_runner.root).as_posix()
    return {**args, "argv": ["git", *argv[3:]], "cwd": safe}
