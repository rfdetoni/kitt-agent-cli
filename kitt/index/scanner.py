"""Repository file scanner with git-native discovery and bounded fallbacks."""
from __future__ import annotations

import fnmatch
import os
import subprocess
from pathlib import Path
from typing import Dict, Iterable, List

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
MANIFEST_SUFFIXES = {".sln": "dotnet", ".csproj": "dotnet", ".fsproj": "dotnet"}
IGNORED_DIRS = {
    ".git", ".kitt", ".venv", "venv", "node_modules", "target", "build",
    "dist", "__pycache__", ".pytest_cache",
}
IGNORED_EXTS = {
    ".pyc", ".pyo", ".pyd", ".so", ".dll", ".exe", ".bin", ".zip",
    ".tar", ".gz", ".png", ".jpg", ".pdf",
}


class RepositoryScanner:
    """Scans workspace files and detects deep monorepo module manifests."""

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
        return any(
            fnmatch.fnmatch(rel_path, pattern)
            or any(fnmatch.fnmatch(part, pattern) for part in parts)
            for pattern in self._kittignore
        )

    def _git_files(self, timeout: float = 5.0) -> list[str] | None:
        try:
            inside = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=str(self.root_path), stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, timeout=2,
            )
            if inside.returncode != 0 or inside.stdout.strip() != "true":
                return None
            out = subprocess.check_output(
                ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                cwd=str(self.root_path), stderr=subprocess.DEVNULL, timeout=timeout,
            )
            return [
                raw.decode("utf-8", errors="surrogateescape")
                for raw in out.split(b"\0") if raw
            ]
        except Exception:
            return None

    @staticmethod
    def _manifest_kind(path: str) -> str | None:
        p = Path(path)
        return MANIFEST_NAMES.get(p.name) or MANIFEST_SUFFIXES.get(p.suffix)

    def detect_modules(
        self,
        max_depth: int | None = None,
        max_manifests: int = 2000,
        max_directories: int = 50_000,
    ) -> List[Dict[str, str]]:
        """Find module roots without an arbitrary depth cut-off.

        Git repositories use ``git ls-files`` so deep monorepos remain cheap.
        Non-Git workspaces use directory/manifests budgets. ``max_depth`` is
        retained as an explicit compatibility limit, but defaults to unlimited.
        """
        modules: list[Dict[str, str]] = []
        seen: set[tuple[str, str]] = set()

        git_files = self._git_files()
        if git_files is not None:
            candidates: Iterable[str] = sorted(git_files)
            for rel_file in candidates:
                if len(modules) >= max_manifests or self._is_ignored(rel_file):
                    continue
                if max_depth is not None:
                    depth = len(Path(rel_file).parent.parts)
                    if depth > max(0, int(max_depth)):
                        continue
                kind = self._manifest_kind(rel_file)
                if not kind:
                    continue
                rel_dir = str(Path(rel_file).parent)
                root = "." if rel_dir in {"", "."} else rel_dir
                manifest_path = os.path.join(root, Path(rel_file).name)
                key = (root, rel_file)
                if key in seen:
                    continue
                seen.add(key)
                modules.append({"root_path": root, "kind": kind, "manifest_path": manifest_path})
        else:
            visited = 0
            for path, dirs, files in os.walk(self.root_path):
                visited += 1
                if visited > max_directories or len(modules) >= max_manifests:
                    break
                dirs[:] = sorted(
                    d for d in dirs
                    if not self._is_ignored(
                        str((Path(path) / d).relative_to(self.root_path))
                    )
                )
                rel = Path(path).relative_to(self.root_path)
                if max_depth is not None and len(rel.parts) >= max(0, int(max_depth)):
                    dirs.clear()
                for filename in sorted(files):
                    kind = self._manifest_kind(filename)
                    if not kind:
                        continue
                    rel_file = str((rel / filename)) if str(rel) != "." else filename
                    if self._is_ignored(rel_file):
                        continue
                    root = "." if str(rel) == "." else str(rel)
                    manifest_path = os.path.join(root, filename)
                    key = (root, rel_file)
                    if key in seen:
                        continue
                    seen.add(key)
                    modules.append(
                        {"root_path": root, "kind": kind, "manifest_path": manifest_path}
                    )
                    if len(modules) >= max_manifests:
                        break

        modules.sort(key=lambda item: (item["root_path"], item["manifest_path"] or ""))
        if not modules:
            modules.append({"root_path": ".", "kind": "generic", "manifest_path": None})
        return modules

    def scan_files(
        self,
        max_files: int = 20000,
        max_file_bytes: int = 512 * 1024,
        max_total_bytes: int = 256 * 1024 * 1024,
    ) -> List[Path]:
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
            try:
                with path.open("rb") as fh:
                    sample = fh.read(4096)
            except OSError:
                return False
            if b"\0" in sample:
                return False
            total_bytes += size
            return True

        git_files = self._git_files()
        if git_files is not None:
            files: list[Path] = []
            for rel in git_files:
                p = self.root_path / rel
                if p.is_file() and accept(p, rel):
                    files.append(p)
                    if len(files) >= max_files:
                        break
            return files

        files = []
        for root, dirs, filenames in os.walk(self.root_path):
            dirs[:] = sorted(
                d for d in dirs
                if not self._is_ignored(str((Path(root) / d).relative_to(self.root_path)))
            )
            for filename in sorted(filenames):
                p = Path(root) / filename
                rel = str(p.relative_to(self.root_path))
                if p.is_file() and accept(p, rel):
                    files.append(p)
                    if len(files) >= max_files:
                        break
            if len(files) >= max_files:
                break
        return files
