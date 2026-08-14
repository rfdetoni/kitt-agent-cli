"""Repository file scanner with git ls-files, scandir fallback, and monorepo module detection."""

from __future__ import annotations

import os
import subprocess
import fnmatch
from pathlib import Path
from typing import List, Dict

MANIFEST_NAMES = {
    "pyproject.toml": "python",
    "setup.py": "python",
    "package.json": "node",
    "pom.xml": "java",
    "build.gradle": "java",
    "build.gradle.kts": "java",
    "settings.gradle": "java",
    "settings.gradle.kts": "java",
    "Cargo.toml": "rust",
    "go.mod": "go",
    "go.work": "go",
}

MANIFEST_SUFFIXES = {
    ".sln": "dotnet",
    ".csproj": "dotnet",
    ".fsproj": "dotnet",
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
        self._kittignore = self._load_kittignore()

    def _load_kittignore(self) -> List[str]:
        path = self.root_path / ".kittignore"
        if not path.exists():
            return []
        patterns = []
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            clean = line.strip()
            if clean and not clean.startswith("#"):
                patterns.append(clean.rstrip("/"))
        return patterns

    def _is_ignored(self, rel_path: str) -> bool:
        parts = Path(rel_path).parts
        if any(part in IGNORED_DIRS for part in parts):
            return True
        for pattern in self._kittignore:
            if fnmatch.fnmatch(rel_path, pattern) or any(fnmatch.fnmatch(part, pattern) for part in parts):
                return True
        return False

    def detect_modules(self) -> List[Dict[str, str]]:
        """Find module roots by manifest files."""
        modules = []
        for path, dirs, files in os.walk(self.root_path):
            dirs[:] = [d for d in dirs if not self._is_ignored(str((Path(path) / d).relative_to(self.root_path)))]
            for file in files:
                kind = MANIFEST_NAMES.get(file) or MANIFEST_SUFFIXES.get(Path(file).suffix)
                if kind:
                    rel_dir = str(Path(path).relative_to(self.root_path))
                    modules.append({
                        "root_path": "." if rel_dir == "." else rel_dir,
                        "kind": kind,
                        "manifest_path": os.path.join(rel_dir, file)
                    })
        if not modules:
            modules.append({"root_path": ".", "kind": "generic", "manifest_path": None})
        return modules

    def scan_files(
        self,
        max_files: int = 20000,
        max_file_bytes: int = 512 * 1024,
        max_total_bytes: int = 256 * 1024 * 1024,
    ) -> List[Path]:
        """Scan workspace files using git ls-files if available, else os.scandir."""
        total_bytes = 0

        def accept(path: Path, rel_path: str) -> bool:
            nonlocal total_bytes
            if path.suffix in IGNORED_EXTS or self._is_ignored(rel_path):
                return False
            try:
                size = path.stat().st_size
            except OSError:
                return False
            if size > max_file_bytes or total_bytes + size > max_total_bytes:
                return False
            total_bytes += size
            return True

        try:
            inside = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=str(self.root_path),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=2,
            )
            is_git = inside.returncode == 0 and inside.stdout.strip() == "true"
        except Exception:
            is_git = False
        if is_git:
            try:
                out = subprocess.check_output(
                    ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                    cwd=str(self.root_path),
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
                files = []
                for raw in out.split(b"\0"):
                    if not raw:
                        continue
                    rel = raw.decode("utf-8", errors="surrogateescape")
                    p = self.root_path / rel
                    if p.is_file() and accept(p, rel):
                        files.append(p)
                        if len(files) >= max_files:
                            break
                return files
            except Exception:
                pass

        # Fallback to os.scandir
        files = []
        for root, dirs, filenames in os.walk(self.root_path):
            dirs[:] = [d for d in dirs if not self._is_ignored(str((Path(root) / d).relative_to(self.root_path)))]
            for filename in filenames:
                p = Path(root) / filename
                rel = str(p.relative_to(self.root_path))
                if p.is_file() and accept(p, rel):
                    files.append(p)
                    if len(files) >= max_files:
                        break
            if len(files) >= max_files:
                break
        return files
