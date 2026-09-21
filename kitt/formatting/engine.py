"""Dynamic, allowlisted formatter execution and deterministic auto-healing."""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from kitt.formatting.contract import FormattingContractManager
from kitt.security.workspace_fs import DEFAULT_MAX_FILE_BYTES, WorkspaceFileSystem
from kitt.validation.generated_content import (
    GeneratedContentError,
    prepare_generated_content,
    validate_generated_content,
)

_FORMATTER_COMMANDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "ruff": ("ruff", ("format", "{file}")),
    "black": ("black", ("--quiet", "{file}")),
    "google-java-format": ("google-java-format", ("--replace", "{file}")),
    "clang-format": ("clang-format", ("-i", "{file}")),
    "ktlint": ("ktlint", ("-F", "{file}")),
    "biome": ("biome", ("format", "--write", "{file}")),
    "prettier": ("prettier", ("--write", "{file}")),
    "gofmt": ("gofmt", ("-w", "{file}")),
    "rustfmt": ("rustfmt", ("{file}",)),
    "swift-format": ("swift-format", ("format", "-i", "{file}")),
    "dart-format": ("dart", ("format", "{file}")),
    "zig-fmt": ("zig", ("fmt", "{file}")),
    "shfmt": ("shfmt", ("-w", "{file}")),
    "stylua": ("stylua", ("{file}",)),
    "rubocop": ("rubocop", ("-A", "--force-exclusion", "{file}")),
}


class DynamicFormattingEngine:
    """Heal every code mutation with deterministic rules and safe local formatters."""

    def __init__(self, root: str | Path, process_runner=None):
        self.root = Path(root).resolve()
        self.fs = WorkspaceFileSystem(self.root, max_file_bytes=DEFAULT_MAX_FILE_BYTES)
        self.process_runner = process_runner
        self.contracts = FormattingContractManager(self.root)

    def prepare_content(
        self,
        path: str,
        content: str,
        *,
        existing_content: str | None = None,
    ):
        _language, language_contract = self.contracts.language_contract(path)
        style = (
            language_contract.get("style")
            if isinstance(language_contract.get("style"), dict)
            else {}
        )
        return prepare_generated_content(
            path,
            content,
            existing_content=existing_content,
            style=style,
        )

    def _local_executable(self, executable: str) -> str | None:
        """Resolve an allowlisted formatter without trusting workspace PATH shims."""
        discovered = shutil.which(executable)
        if discovered:
            candidate = Path(discovered).resolve()
            try:
                candidate.relative_to(self.root)
            except ValueError:
                return str(candidate)
            if os.getenv("KITT_TRUST_PROJECT_FORMATTERS", "").strip().casefold() in {
                "1", "true", "yes", "on", "enabled",
            }:
                return str(candidate)
            return None

        # Project-local Node formatters are executable code from the workspace.
        # They are opt-in rather than silently trusted by auto-healing.
        if os.getenv("KITT_TRUST_PROJECT_FORMATTERS", "").strip().casefold() not in {
            "1", "true", "yes", "on", "enabled",
        }:
            return None
        suffix = ".cmd" if os.name == "nt" else ""
        for base in ("node_modules/.bin", "frontend/node_modules/.bin"):
            candidate = (self.root / base / f"{executable}{suffix}").resolve()
            if candidate.is_file():
                return str(candidate)
        return None

    def _formatter_argv(self, formatter_id: str, relative: str) -> list[str] | None:
        spec = _FORMATTER_COMMANDS.get(formatter_id)
        if spec is None:
            return None
        executable, tail = spec
        resolved = self._local_executable(executable)
        if not resolved:
            return None
        return [resolved, *(part.replace("{file}", relative) for part in tail)]

    def format_paths(self, paths: list[str]) -> dict[str, dict[str, Any]]:
        """Heal changed files in place before their undo journal is finalized.

        Workspace contracts can choose only known formatter IDs. External formatter
        failure is fail-soft because deterministic healing and the post-edit validator
        remain authoritative; invalid final syntax is still rejected by the gate.
        """
        result: dict[str, dict[str, Any]] = {}
        for raw in dict.fromkeys(str(path) for path in paths if path):
            try:
                relative = self.fs.relative(raw)
                data = self.fs.read(relative)
                before = data.content.decode("utf-8", errors="strict")
            except (FileNotFoundError, OSError, UnicodeError, ValueError) as exc:
                result[str(raw)] = {"ok": False, "error": str(exc), "strategy": "unavailable"}
                continue

            language, language_contract = self.contracts.language_contract(relative)
            try:
                style = (
                    language_contract.get("style")
                    if isinstance(language_contract.get("style"), dict)
                    else {}
                )
                prepared = prepare_generated_content(
                    relative,
                    before,
                    existing_content=before,
                    style=style,
                )
            except GeneratedContentError as exc:
                result[relative] = {
                    "ok": False, "language": language, "strategy": "validate",
                    "error": str(exc),
                }
                continue

            current = prepared.content
            current_hash = data.sha256
            healed = current != before
            if healed:
                try:
                    current_hash = self.fs.atomic_write(
                        relative,
                        current,
                        expected_exists=True,
                        expected_sha256=data.sha256,
                        max_bytes=DEFAULT_MAX_FILE_BYTES,
                    )
                except Exception as exc:
                    result[relative] = {
                        "ok": False, "language": language, "strategy": prepared.strategy,
                        "error": str(exc),
                    }
                    continue

            formatter_used = None
            formatter_error = None
            order = (
                language_contract.get("formatter_order", [])
                if isinstance(language_contract, dict)
                else []
            )
            if self.process_runner is not None and isinstance(order, list):
                for formatter_id in order[:8]:
                    argv = self._formatter_argv(str(formatter_id), relative)
                    if not argv:
                        continue
                    execution = self.process_runner.run(
                        argv,
                        cwd=".",
                        timeout_seconds=30,
                        sandbox_profile="workspace-write",
                        require_strong_sandbox=False,
                    )
                    formatter_used = str(formatter_id)
                    if execution.returncode == 0:
                        self.contracts.remember_formatter_success(
                            language, formatter_used
                        )
                        break
                    formatter_error = (
                        execution.stderr or execution.stdout
                        or f"formatter exited with code {execution.returncode}"
                    )[:1000]
                    formatter_used = None

            try:
                final_data = self.fs.read(relative)
                final_text = final_data.content.decode("utf-8", errors="strict")
                validate_generated_content(relative, final_text)
                result[relative] = {
                    "ok": True,
                    "language": language,
                    "strategy": (
                        f"formatter:{formatter_used}"
                        if formatter_used
                        else prepared.strategy
                    ),
                    "healed": healed or final_text != before,
                    "formatter": formatter_used,
                    "formatter_error": formatter_error,
                    "content_hash": final_data.sha256,
                }
            except Exception as exc:
                result[relative] = {
                    "ok": False, "language": language,
                    "strategy": f"formatter:{formatter_used}" if formatter_used else prepared.strategy,
                    "formatter": formatter_used, "error": str(exc),
                }
        return result
