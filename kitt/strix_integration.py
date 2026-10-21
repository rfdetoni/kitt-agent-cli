"""Optional execution of installed Strix; never reimplement its scanner."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit


def strix_status() -> dict[str, object]:
    path = shutil.which("strix")
    return {"installed": path is not None, "executable": path}


def scan_strix(
    *,
    workspace: str | Path,
    target: str,
    authorized: bool,
    timeout: int = 1800,
) -> int:
    """Run only after explicit scope authorization; argv-only and workspace-bound files."""
    if not authorized:
        raise PermissionError("Strix requires --authorized for this specific target")
    executable = shutil.which("strix")
    if not executable:
        raise FileNotFoundError("Strix is not installed (install strix-agent first)")
    root = Path(workspace).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("workspace is not a directory")
    if not target or not target.strip():
        raise ValueError("target must be nonempty")
    target = target.strip()
    parsed = urlsplit(target)
    windows_drive_path = (
        len(target) > 2 and target[0].isalpha()
        and target[1] == ":" and target[2] in ("/", "\\")
    )
    if parsed.scheme and not windows_drive_path:
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("Only explicit http(s) target URLs are accepted")
        if parsed.username or parsed.password:
            raise ValueError("Credentials must not be embedded in target URLs")
    else:
        path = (root / target).resolve(strict=True)
        if not path.is_relative_to(root):
            raise PermissionError("Local Strix targets must remain inside the workspace")
        target = str(path)
    if not 1 <= timeout <= 3600:
        raise ValueError("timeout must be between 1 and 3600 seconds")
    try:
        return subprocess.run(
            [executable, "--target", target],
            cwd=str(root), shell=False, timeout=timeout, check=False,
        ).returncode
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("Strix scan exceeded its timeout") from exc
