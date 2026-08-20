from __future__ import annotations

from pathlib import Path


def _resolve_git_dir(workspace_path: str | Path) -> Path | None:
    root = Path(workspace_path).expanduser().resolve()
    marker = root / ".git"
    if marker.is_dir():
        return marker
    if not marker.is_file():
        return None
    try:
        payload = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not payload.startswith("gitdir:"):
        return None
    target = payload.split(":", 1)[1].strip()
    git_dir = Path(target)
    if not git_dir.is_absolute():
        git_dir = (root / git_dir).resolve()
    return git_dir if git_dir.is_dir() else None


def read_git_branch_name(workspace_path: str | Path) -> str:
    git_dir = _resolve_git_dir(workspace_path)
    if git_dir is None:
        return ""
    head = git_dir / "HEAD"
    try:
        payload = head.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if payload.startswith("ref:"):
        ref = payload.split(":", 1)[1].strip()
        prefix = "refs/heads/"
        return ref[len(prefix):] if ref.startswith(prefix) else ref.rsplit("/", 1)[-1]
    return f"detached:{payload[:7]}" if payload else ""
