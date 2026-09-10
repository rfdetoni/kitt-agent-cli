from __future__ import annotations

import json
import math
import shutil
from pathlib import Path
from typing import Any, Iterable

from kitt.tools.process_runner import ProcessRunner


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

        capture_limit = max(4096, min(int(max_output_bytes), 8 * 1024 * 1024))
        runner = ProcessRunner(str(self.root), max_output_bytes=capture_limit)
        completed = runner.run(
            command,
            timeout_seconds=max(1, min(math.ceil(float(timeout_seconds)), 120)),
            env={"NO_COLOR": "1", "CLICOLOR": "0"},
        )
        stderr = completed.stderr[:16_384]
        if completed.timed_out:
            raise RuntimeError("ast-grep timed out")
        if completed.cancelled:
            raise RuntimeError("ast-grep was cancelled")
        if completed.returncode not in (0, 1) and not completed.stdout:
            raise RuntimeError(f"ast-grep failed ({completed.returncode}): {stderr[:2000]}")
        if completed.truncated and completed.stdout_total_bytes > len(completed.stdout.encode("utf-8")):
            raise RuntimeError(
                f"ast-grep output exceeded the {capture_limit}-byte capture limit"
            )
        try:
            payload = json.loads(completed.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"ast-grep returned invalid JSON: {stderr[:1000]}") from exc
        if not isinstance(payload, list):
            payload = [payload]
        matches = payload[:limit]
        return {
            "backend": "ast-grep",
            "matches": matches,
            "returned": len(matches),
            "truncated": len(payload) > limit or completed.truncated,
            "exit_code": completed.returncode,
        }
