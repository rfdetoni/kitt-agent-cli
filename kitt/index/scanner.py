"""Repository file scanner with git-native discovery and symlink-safe fallbacks."""
from __future__ import annotations

import fnmatch
import os
import stat
import subprocess
from pathlib import Path
from typing import Dict, Iterable, List

from kitt.security.workspace_fs import WorkspaceFileSystem

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
    """Scan workspace files without following symlinks or special files."""

    def __init__(self, root_dir: str | Path):
        self.root_path = Path(root_dir).resolve()
        self.workspace_fs = WorkspaceFileSystem(self.root_path)
        self._kittignore = self._load_kittignore()

    def _load_kittignore(self) -> List[str]:
        try:
            text, _ = self.workspace_fs.read_text(".kittignore", max_bytes=512 * 1024)
        except (FileNotFoundError, PermissionError, ValueError, OSError):
            return []
        patterns = []
        for line in text.splitlines():
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
                cwd=str(self.root_path),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=2,
            )
            if inside.returncode != 0 or inside.stdout.strip() != "true":
                return None
            out = subprocess.check_output(
                ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                cwd=str(self.root_path),
                stderr=subprocess.DEVNULL,
                timeout=timeout,
            )
            result = []
            for raw in out.split(b"\0"):
                if not raw:
                    continue
                candidate = raw.decode("utf-8", errors="surrogateescape")
                try:
                    normalized = self.workspace_fs.relative(candidate)
                except PermissionError:
                    continue
                if normalized != ".":
                    result.append(normalized)
            return result
        except Exception:
            return None

    @staticmethod
    def _manifest_kind(path: str) -> str | None:
        p = Path(path)
        return MANIFEST_NAMES.get(p.name) or MANIFEST_SUFFIXES.get(p.suffix)

    def _safe_lstat_regular(self, rel_path: str) -> os.stat_result | None:
        """Cheap discovery check; final content reads still go through WorkspaceFileSystem."""
        try:
            normalized = self.workspace_fs.relative(rel_path)
            if normalized == ".":
                return None
            st = os.lstat(self.root_path / normalized)
        except (OSError, PermissionError):
            return None
        return st if stat.S_ISREG(st.st_mode) else None

    def detect_modules(
        self,
        max_depth: int | None = None,
        max_manifests: int = 2000,
        max_directories: int = 50_000,
    ) -> List[Dict[str, str]]:
        modules: list[Dict[str, str]] = []
        seen: set[tuple[str, str]] = set()

        git_files = self._git_files()
        if git_files is not None:
            candidates: Iterable[str] = sorted(git_files)
            for rel_file in candidates:
                if len(modules) >= max_manifests or self._is_ignored(rel_file):
                    continue
                if self._safe_lstat_regular(rel_file) is None:
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
                modules.append(
                    {"root_path": root, "kind": kind, "manifest_path": manifest_path}
                )
        else:
            visited = 0
            for path, dirs, files in os.walk(self.root_path, followlinks=False):
                visited += 1
                if visited > max_directories or len(modules) >= max_manifests:
                    break
                safe_dirs = []
                for directory in sorted(dirs):
                    candidate = Path(path) / directory
                    try:
                        rel_dir = str(candidate.relative_to(self.root_path))
                        st = os.lstat(candidate)
                    except OSError:
                        continue
                    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                        continue
                    if not self._is_ignored(rel_dir):
                        safe_dirs.append(directory)
                dirs[:] = safe_dirs

                rel = Path(path).relative_to(self.root_path)
                if max_depth is not None and len(rel.parts) >= max(0, int(max_depth)):
                    dirs.clear()
                for filename in sorted(files):
                    kind = self._manifest_kind(filename)
                    if not kind:
                        continue
                    rel_file = str(rel / filename) if str(rel) != "." else filename
                    if self._is_ignored(rel_file) or self._safe_lstat_regular(rel_file) is None:
                        continue
                    root = "." if str(rel) == "." else str(rel)
                    key = (root, rel_file)
                    if key in seen:
                        continue
                    seen.add(key)
                    modules.append(
                        {
                            "root_path": root,
                            "kind": kind,
                            "manifest_path": os.path.join(root, filename),
                        }
                    )
                    if len(modules) >= max_manifests:
                        break

        modules.sort(key=lambda item: (item["root_path"], item["manifest_path"] or ""))
        if not modules:
            modules.append({"root_path": ".", "kind": "generic", "manifest_path": None})
        return modules

    def scan_relative_files(
        self,
        max_files: int = 20000,
        max_file_bytes: int = 512 * 1024,
        max_total_bytes: int = 256 * 1024 * 1024,
    ) -> List[str]:
        total_bytes = 0
        results: list[str] = []

        def accept(rel_path: str) -> bool:
            nonlocal total_bytes
            path = Path(rel_path)
            if path.suffix in IGNORED_EXTS or self._is_ignored(rel_path):
                return False
            st = self._safe_lstat_regular(rel_path)
            if st is None:
                return False
            if st.st_size > max_file_bytes or total_bytes + st.st_size > max_total_bytes:
                return False
            try:
                sample = self.workspace_fs.read_prefix(
                    rel_path,
                    max_bytes=min(4096, max_file_bytes),
                ).content
            except (OSError, PermissionError, ValueError):
                return False
            if b"\0" in sample:
                return False
            total_bytes += st.st_size
            return True

        git_files = self._git_files()
        if git_files is not None:
            for rel in git_files:
                if accept(rel):
                    results.append(rel)
                    if len(results) >= max_files:
                        break
            return results

        for root, dirs, filenames in os.walk(self.root_path, followlinks=False):
            safe_dirs = []
            for directory in sorted(dirs):
                candidate = Path(root) / directory
                try:
                    rel_dir = str(candidate.relative_to(self.root_path))
                    st = os.lstat(candidate)
                except OSError:
                    continue
                if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                    continue
                if not self._is_ignored(rel_dir):
                    safe_dirs.append(directory)
            dirs[:] = safe_dirs

            for filename in sorted(filenames):
                rel = str((Path(root) / filename).relative_to(self.root_path))
                if accept(rel):
                    results.append(rel)
                    if len(results) >= max_files:
                        return results
        return results

    def scan_files(
        self,
        max_files: int = 20000,
        max_file_bytes: int = 512 * 1024,
        max_total_bytes: int = 256 * 1024 * 1024,
    ) -> List[Path]:
        """Compatibility facade returning lexical Paths after safe discovery."""
        return [
            self.root_path / rel
            for rel in self.scan_relative_files(
                max_files=max_files,
                max_file_bytes=max_file_bytes,
                max_total_bytes=max_total_bytes,
            )
        ]
