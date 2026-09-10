"""Fast, strong post-edit validation gates with optimistic rollback support."""
from __future__ import annotations

import ast
import json
import os
import shutil
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from kitt.security.workspace_fs import WorkspaceFileSystem


@dataclass
class GateDiagnostic:
    path: str
    validator: str
    ok: bool
    message: str = ""


@dataclass
class PostEditReport:
    ok: bool
    checked: int
    skipped: int
    diagnostics: list[GateDiagnostic] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "checked": self.checked,
            "skipped": self.skipped,
            "diagnostics": [
                {"path": item.path, "validator": item.validator, "ok": item.ok, "message": item.message}
                for item in self.diagnostics
            ],
        }


class PostEditValidator:
    """Validate changed files only, with optional local Semgrep enforcement."""

    def __init__(self, root: str | Path, process_runner=None):
        self.root = Path(root).resolve()
        self.fs = WorkspaceFileSystem(self.root)
        self.process_runner = process_runner
        self._semgrep = None

    def _external(self, argv: list[str], path: str, validator: str) -> GateDiagnostic:
        if self.process_runner is None or shutil.which(argv[0]) is None:
            return GateDiagnostic(path, validator, True, "validator unavailable; skipped")
        result = self.process_runner.run(argv, timeout_seconds=10)
        if result.returncode == 0:
            return GateDiagnostic(path, validator, True)
        detail = (result.stderr or result.stdout or "syntax validation failed").strip()
        return GateDiagnostic(path, validator, False, detail[:1200])

    def _validate_one(self, relative: str) -> tuple[GateDiagnostic | None, bool]:
        suffix = Path(relative).suffix.casefold()
        try:
            data = self.fs.read(relative)
        except FileNotFoundError:
            return None, True
        text = data.content.decode("utf-8", errors="strict")
        validator_name = {".py": "python.ast", ".json": "json", ".toml": "tomllib", ".xml": "xml", ".xhtml": "xml", ".svg": "xml"}.get(suffix, suffix.lstrip(".") or "syntax")
        try:
            if suffix == ".py":
                ast.parse(text, filename=relative)
                return GateDiagnostic(relative, "python.ast", True), False
            if suffix == ".json":
                json.loads(text)
                return GateDiagnostic(relative, "json", True), False
            if suffix == ".toml":
                tomllib.loads(text)
                return GateDiagnostic(relative, "tomllib", True), False
            if suffix in {".xml", ".xhtml", ".svg"}:
                ET.fromstring(text)
                return GateDiagnostic(relative, "xml", True), False
        except (SyntaxError, ValueError, UnicodeError, ET.ParseError) as exc:
            return GateDiagnostic(relative, validator_name, False, str(exc)), False
        if suffix in {".js", ".mjs", ".cjs"}:
            return self._external(["node", "--check", relative], relative, "node --check"), False
        if suffix == ".rs":
            return self._external(["rustfmt", "--emit", "stdout", relative], relative, "rustfmt parse"), False
        if suffix == ".go":
            return self._external(["gofmt", relative], relative, "gofmt parse"), False
        return None, True

    @staticmethod
    def _semgrep_enabled() -> bool:
        return os.getenv("KITT_SEMGREP_GATE", "").strip().lower() in {"1", "true", "yes", "on", "enabled"}

    def _semgrep_diagnostics(self, paths: list[str]) -> list[GateDiagnostic]:
        if not self._semgrep_enabled() or not paths:
            return []
        try:
            if self._semgrep is None:
                from kitt.integrations.semgrep import SemgrepAdapter
                self._semgrep = SemgrepAdapter(self.root)
            if not self._semgrep.available:
                return [GateDiagnostic("<changed-files>", "semgrep", True, "KITT_SEMGREP_GATE enabled but semgrep is unavailable; skipped")]
            result = self._semgrep.scan_changed(paths=paths)
        except FileNotFoundError as exc:
            return [GateDiagnostic("<changed-files>", "semgrep", True, f"local Semgrep config unavailable; skipped: {exc}")]
        except Exception as exc:
            return [GateDiagnostic("<changed-files>", "semgrep", False, str(exc)[:1200])]
        findings = result.get("findings", [])
        if not findings:
            return [GateDiagnostic("<changed-files>", "semgrep", True, "no findings" + (" (cache)" if result.get("cache_hit") else ""))]
        diagnostics: list[GateDiagnostic] = []
        for finding in findings[:200]:
            if not isinstance(finding, dict):
                continue
            path = str(finding.get("path") or "<changed-files>")
            check_id = str(finding.get("check_id") or "semgrep")
            extra = finding.get("extra") if isinstance(finding.get("extra"), dict) else {}
            message = str(extra.get("message") or check_id)
            diagnostics.append(GateDiagnostic(path, f"semgrep:{check_id}", False, message[:1200]))
        return diagnostics or [GateDiagnostic("<changed-files>", "semgrep", False, f"{len(findings)} finding(s)")]

    def validate_paths(self, paths: Iterable[str]) -> PostEditReport:
        diagnostics: list[GateDiagnostic] = []
        skipped = 0
        changed: list[str] = []
        for raw in dict.fromkeys(str(path) for path in paths if path):
            try:
                relative = self.fs.relative(raw)
                diagnostic, was_skipped = self._validate_one(relative)
                if self.fs.exists_regular(relative):
                    changed.append(relative)
            except (OSError, UnicodeError, ValueError) as exc:
                diagnostic = GateDiagnostic(str(raw), "workspace-read", False, str(exc))
                was_skipped = False
            if was_skipped:
                skipped += 1
            if diagnostic is not None:
                diagnostics.append(diagnostic)
        diagnostics.extend(self._semgrep_diagnostics(changed))
        return PostEditReport(ok=all(item.ok for item in diagnostics), checked=len(diagnostics), skipped=skipped, diagnostics=diagnostics)
