"""Path policy for canonical workspace containment and sensitive file protection."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Tuple, Optional

FORBIDDEN_NAMES = {
    ".ssh", ".gnupg", ".aws", ".azure", ".kube", "credentials", "secrets", "id_rsa"
}

FORBIDDEN_EXTS = {".pem", ".key", ".p12"}


class PathPolicy:
    """Enforces canonical path containment in workspace and blocks sensitive files."""

    def __init__(self, root_dir: str | Path):
        self.root_path = Path(root_dir).resolve()

    def validate_path(self, rel_or_abs_path: str | Path) -> Tuple[bool, Optional[Path], str]:
        """Validate path containment and check for sensitive files.

        Returns (is_safe, resolved_path, error_message).
        """
        if not rel_or_abs_path:
            return False, None, "Path cannot be empty"

        try:
            raw_path = Path(rel_or_abs_path)
            if raw_path.is_absolute():
                target = raw_path.resolve(strict=False)
            else:
                target = (self.root_path / raw_path).resolve(strict=False)

            # Containment check
            try:
                target.relative_to(self.root_path)
            except ValueError:
                return False, None, f"Access denied: path '{rel_or_abs_path}' is outside workspace"

            # Check forbidden path names or extensions
            parts = target.parts
            for part in parts:
                low = part.lower()
                if low in FORBIDDEN_NAMES or low.startswith(".env"):
                    return False, None, f"Access denied: sensitive directory/file '{part}' is forbidden"

            if target.suffix.lower() in FORBIDDEN_EXTS:
                return False, None, f"Access denied: sensitive extension '{target.suffix}' is forbidden"

            return True, target, ""
        except Exception as exc:
            return False, None, f"Path validation error: {exc}"
