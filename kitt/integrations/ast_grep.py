from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable


class AstGrepUnavailable(RuntimeError):
    pass


class AstGrepAdapter:
    """Read-only structural search adapter.

    KITT intentionally does not expose ast-grep's direct update mode here.
    Structural rewrites must be rendered as a proposed patch and applied by the
    normal KITT mutation/approval pipeline.
    """

    def __init__(self, root_dir: str | Path):
        self.root = Path(root_dir).resolve()
        self.executable = shutil.which("ast-grep")

    @property
    def available(self) -> bool:
        return bool(self.executable)

    def _safe_path(self, raw: str | Path) -> str:
        value = Path(raw)
        target = (self.root / value).resolve() if not value.is_absolute() else value.resolve()
        try:
            relative = target.relative_to(self.root)
        except ValueError as exc:
            raise PermissionError(f"ast-grep path escapes workspace: {raw}") from exc
        return relative.as_posix() or "."

    def search(
        self,
        pattern: str,
        *,
        paths: Iterable[str] = (".",),
        language: str | None = None,
        globs: Iterable[str] = (),
        limit: int = 100,
        timeout_seconds: float = 15.0,
        max_output_bytes: int = 512 * 1024,
    ) -> dict[str, Any]:
        if not self.executable:
            raise AstGrepUnavailable("ast-grep is not installed or not available in PATH")
        pattern = str(pattern or "")
        if not pattern or len(pattern) > 16_384:
            raise ValueError("ast-grep pattern must contain 1..16384 characters")
        limit = max(1, min(int(limit), 1000))
        safe_paths = [self._safe_path(path) for path in list(paths)[:64]] or ["."]

        command = [
            self.executable,
            "run",
            "--pattern",
            pattern,
            "--json=compact",
            "--color",
            "never",
        ]
        if language:
            safe_language = str(language).strip()
            if not safe_language.replace("-", "").replace("_", "").isalnum():
                raise ValueError("Invalid ast-grep language")
            command.extend(["--lang", safe_language])
        for glob in list(globs)[:32]:
            value = str(glob).strip()
            if value and len(value) <= 512:
                command.extend(["--globs", value])
        command.extend(safe_paths)

        env = dict(os.environ)
        env.update({"NO_COLOR": "1", "CLICOLOR": "0"})
        completed = subprocess.run(
            command,
            cwd=self.root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            timeout=max(0.5, min(float(timeout_seconds), 120.0)),
            check=False,
            env=env,
        )
        raw = completed.stdout[: max(1024, min(int(max_output_bytes), 8 * 1024 * 1024))]
        stderr = completed.stderr[:16_384].decode("utf-8", "replace")
        if completed.returncode not in (0, 1) and not raw:
            raise RuntimeError(f"ast-grep failed ({completed.returncode}): {stderr[:2000]}")
        try:
            payload = json.loads(raw.decode("utf-8", "replace") or "[]")
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"ast-grep returned invalid JSON: {stderr[:1000]}") from exc
        if not isinstance(payload, list):
            payload = [payload]
        matches = payload[:limit]
        return {
            "backend": "ast-grep",
            "matches": matches,
            "returned": len(matches),
            "truncated": len(payload) > limit or len(completed.stdout) > len(raw),
            "exit_code": completed.returncode,
        }
