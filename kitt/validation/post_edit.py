"""Fast, strong post-edit validation gates with optimistic rollback support."""
from __future__ import annotations

import ast
import json
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
                {
                    "path": item.path,
                    "validator": item.validator,
                    "ok": item.ok,
                    "message": item.message,
                }
                for item in self.diagnostics
            ],
        }


class PostEditValidator:
    """Validate only formats where KITT can make a high-confidence decision."""

    def __init__(self, root: str | Path, process_runner=None):
        self.root = Path(root).resolve()
        self.fs = WorkspaceFileSystem(self.root)
        self.process_runner = process_runner

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

        validator_name = {
            ".py": "python.ast",
            ".json": "json",
            ".toml": "tomllib",
            ".xml": "xml",
            ".xhtml": "xml",
            ".svg": "xml",
        }.get(suffix, suffix.lstrip(".") or "syntax")

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
            return self._external(
                ["rustfmt", "--emit", "stdout", relative], relative, "rustfmt parse"
            ), False
        if suffix == ".go":
            return self._external(["gofmt", relative], relative, "gofmt parse"), False
        return None, True

    def validate_paths(self, paths: Iterable[str]) -> PostEditReport:
        diagnostics: list[GateDiagnostic] = []
        skipped = 0
        for raw in dict.fromkeys(str(path) for path in paths if path):
            try:
                relative = self.fs.relative(raw)
                diagnostic, was_skipped = self._validate_one(relative)
            except (OSError, UnicodeError, ValueError) as exc:
                diagnostic = GateDiagnostic(str(raw), "workspace-read", False, str(exc))
                was_skipped = False
            if was_skipped:
                skipped += 1
            if diagnostic is not None:
                diagnostics.append(diagnostic)
        return PostEditReport(
            ok=all(item.ok for item in diagnostics),
            checked=len(diagnostics),
            skipped=skipped,
            diagnostics=diagnostics,
        )
