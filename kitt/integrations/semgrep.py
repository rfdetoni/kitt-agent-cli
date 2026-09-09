from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable


class SemgrepUnavailable(RuntimeError):
    pass


class SemgrepAdapter:
    """Bounded local Semgrep CE adapter.

    The adapter deliberately requires a workspace-local configuration by
    default. It never selects `--config=auto`, preventing an apparently local
    security scan from silently adding a registry/network dependency.
    """

    _DEFAULT_CONFIGS = (".semgrep.yml", ".semgrep.yaml", "semgrep.yml", "semgrep.yaml")

    def __init__(self, root_dir: str | Path):
        self.root = Path(root_dir).resolve()
        self.executable = shutil.which("semgrep")

    @property
    def available(self) -> bool:
        return bool(self.executable)

    def _safe_path(self, raw: str | Path) -> Path:
        value = Path(raw)
        target = (self.root / value).resolve() if not value.is_absolute() else value.resolve()
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise PermissionError(f"Semgrep path escapes workspace: {raw}") from exc
        return target

    def _config(self, config_path: str | None) -> Path:
        if config_path:
            config = self._safe_path(config_path)
            if not config.is_file():
                raise FileNotFoundError(f"Semgrep config not found: {config_path}")
            return config
        for candidate in self._DEFAULT_CONFIGS:
            path = self.root / candidate
            if path.is_file() and not path.is_symlink():
                return path
        raise FileNotFoundError(
            "No local Semgrep config found. Add .semgrep.yml/.semgrep.yaml or pass config_path; "
            "KITT does not use --config=auto implicitly."
        )

    def scan(
        self,
        *,
        paths: Iterable[str] = (".",),
        config_path: str | None = None,
        max_findings: int = 200,
        timeout_seconds: float = 60.0,
        max_output_bytes: int = 2 * 1024 * 1024,
    ) -> dict[str, Any]:
        if not self.executable:
            raise SemgrepUnavailable("semgrep is not installed or not available in PATH")
        config = self._config(config_path)
        max_findings = max(1, min(int(max_findings), 5000))
        targets = [self._safe_path(path) for path in list(paths)[:64]] or [self.root]
        relative_targets = [
            path.relative_to(self.root).as_posix() if path != self.root else "."
            for path in targets
        ]

        command = [
            self.executable,
            "scan",
            "--json",
            "--metrics",
            "off",
            "--config",
            config.relative_to(self.root).as_posix(),
            *relative_targets,
        ]
        env = dict(os.environ)
        env.update({"SEMGREP_SEND_METRICS": "off", "NO_COLOR": "1", "CLICOLOR": "0"})
        completed = subprocess.run(
            command,
            cwd=self.root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            timeout=max(1.0, min(float(timeout_seconds), 600.0)),
            check=False,
            env=env,
        )
        cap = max(64 * 1024, min(int(max_output_bytes), 16 * 1024 * 1024))
        raw = completed.stdout[:cap]
        stderr = completed.stderr[:32_768].decode("utf-8", "replace")
        if not raw:
            if completed.returncode != 0:
                raise RuntimeError(f"Semgrep failed ({completed.returncode}): {stderr[:2000]}")
            payload: dict[str, Any] = {}
        else:
            try:
                decoded = json.loads(raw.decode("utf-8", "replace"))
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Semgrep returned invalid JSON: {stderr[:1000]}") from exc
            payload = decoded if isinstance(decoded, dict) else {"results": decoded}

        results = payload.get("results")
        findings = results if isinstance(results, list) else []
        bounded = findings[:max_findings]
        errors = payload.get("errors")
        return {
            "backend": "semgrep",
            "config": config.relative_to(self.root).as_posix(),
            "findings": bounded,
            "errors": errors[:100] if isinstance(errors, list) else [],
            "returned": len(bounded),
            "total_findings": len(findings),
            "truncated": len(findings) > max_findings or len(completed.stdout) > len(raw),
            "exit_code": completed.returncode,
        }
