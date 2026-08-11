from pathlib import Path
from typing import Tuple, Optional, Set

FORBIDDEN_NAMES: Set[str] = {".git", ".env"}

class WorkspacePathPolicy:
    """Centralized path validation enforcing workspace boundaries and protected file security."""

    def __init__(self, root_dir: str = "."):
        self.root_path = Path(root_dir).resolve()

    def validate_path(self, file_path: str) -> Tuple[bool, Optional[Path], Optional[str]]:
        try:
            raw_p = Path(file_path)
            full_p = (self.root_path / raw_p).resolve() if not raw_p.is_absolute() else raw_p.resolve()

            if not full_p.is_relative_to(self.root_path):
                return False, None, f"Path containment violation: '{file_path}' resolves outside workspace ({self.root_path})."

            rel = full_p.relative_to(self.root_path)
            for part in rel.parts:
                if part in FORBIDDEN_NAMES or part.startswith(".env"):
                    return False, None, f"Access denied to protected path '{rel}'."

            return True, full_p, None
        except Exception as e:
            return False, None, f"Path validation error: {e}"
