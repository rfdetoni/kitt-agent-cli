"""Repository file scanner with git ls-files, scandir fallback, and monorepo module detection."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import List, Dict, Set, Tuple

MANIFEST_NAMES = {
    "pyproject.toml": "python",
    "setup.py": "python",
    "package.json": "node",
    "pom.xml": "java",
    "build.gradle": "java",
    "Cargo.toml": "rust",
    "go.mod": "go"
}

IGNORED_DIRS = {
    ".git", ".kitt", ".venv", "venv", "node_modules", "target", "build", "dist", "__pycache__", ".pytest_cache"
}

IGNORED_EXTS = {
    ".pyc", ".pyo", ".pyd", ".so", ".dll", ".exe", ".bin", ".zip", ".tar", ".gz", ".png", ".jpg", ".pdf"
}


class RepositoryScanner:
    """Scans workspace files, detects monorepo modules, and filters ignores."""

    def __init__(self, root_dir: str | Path):
        self.root_path = Path(root_dir).resolve()

    def detect_modules(self) -> List[Dict[str, str]]:
        """Find module roots by manifest files."""
        modules = []
        for path, dirs, files in os.walk(self.root_path):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
            for file in files:
                if file in MANIFEST_NAMES:
                    rel_dir = str(Path(path).relative_to(self.root_path))
                    modules.append({
                        "root_path": "." if rel_dir == "." else rel_dir,
                        "kind": MANIFEST_NAMES[file],
                        "manifest_path": os.path.join(rel_dir, file)
                    })
        if not modules:
            modules.append({"root_path": ".", "kind": "generic", "manifest_path": None})
        return modules

    def scan_files(self, max_files: int = 10000) -> List[Path]:
        """Scan workspace files using git ls-files if available, else os.scandir."""
        git_dir = self.root_path / ".git"
        if git_dir.exists():
            try:
                out = subprocess.check_output(
                    ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                    cwd=str(self.root_path),
                    stderr=subprocess.DEVNULL,
                    text=True
                )
                files = []
                for line in out.splitlines():
                    p = self.root_path / line.strip()
                    if p.is_file() and p.suffix not in IGNORED_EXTS:
                        files.append(p)
                        if len(files) >= max_files:
                            break
                return files
            except Exception:
                pass

        # Fallback to os.scandir
        files = []
        for root, dirs, filenames in os.walk(self.root_path):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
            for filename in filenames:
                p = Path(root) / filename
                if p.is_file() and p.suffix not in IGNORED_EXTS:
                    files.append(p)
                    if len(files) >= max_files:
                        break
            if len(files) >= max_files:
                break
        return files
