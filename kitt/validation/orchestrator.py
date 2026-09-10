"""Unified, bounded verification for agent edits.

The orchestrator deliberately composes KITT's existing validators and process
runner instead of introducing a second validation framework.  It is designed
for the tool loop: failures are returned to the model as ordinary tool
failures, allowing the existing loop to repair and revalidate before success.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from kitt.tools.build_detector import BuildDetector
from kitt.validation.post_edit import GateDiagnostic, PostEditValidator


_TRUE = {"1", "true", "yes", "on", "enabled"}


@dataclass
class VerificationReport:
    ok: bool
    diagnostics: list[GateDiagnostic] = field(default_factory=list)
    command: list[str] | None = None
    command_returncode: int | None = None
    command_output: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "diagnostics": [
                {
                    "path": item.path,
                    "validator": item.validator,
                    "ok": item.ok,
                    "message": item.message,
                }
                for item in self.diagnostics
            ],
            "command": self.command,
            "command_returncode": self.command_returncode,
            "command_output": self.command_output[:4000],
        }

    def failure_message(self) -> str:
        parts = [
            f"{item.path}: {item.validator}: {item.message or 'failed'}"
            for item in self.diagnostics
            if not item.ok
        ]
        if self.command_returncode not in (None, 0):
            parts.append(
                f"verification command failed ({self.command_returncode}): "
                f"{self.command_output[:2400]}"
            )
        return "\n".join(parts) or "verification failed"


class VerificationOrchestrator:
    """Run the cheapest strong checks first and bounded project tests second."""

    def __init__(self, root: str | Path, process_runner=None):
        self.root = Path(root).resolve()
        self.process_runner = process_runner
        self.validator = PostEditValidator(self.root, process_runner)
        self.detector = BuildDetector(str(self.root))

    def _targeted_command(self, paths: list[str]) -> list[str] | None:
        # Prefer paired Python tests; this makes verification useful inside the
        # repair loop without running an arbitrarily large suite after every edit.
        python_paths = [Path(p) for p in paths if p.endswith(".py")]
        paired: list[str] = []
        for path in python_paths:
            candidate = self.root / "tests" / f"test_{path.stem}.py"
            if candidate.is_file():
                paired.append(str(candidate.relative_to(self.root)))
        if paired:
            return ["python3", "-m", "pytest", "-q", *paired]

        # Full project verification is opt-in for per-edit loops. Goal quality
        # gates still provide mandatory project-wide checks at goal completion.
        if os.getenv("KITT_AGENT_VERIFY_FULL", "").strip().lower() in _TRUE:
            return self.detector.detect_test_command(paths)
        return None

    def verify(self, paths: Iterable[str]) -> VerificationReport:
        unique = list(dict.fromkeys(str(path) for path in paths if path))[:64]
        syntax = self.validator.validate_paths(unique)
        diagnostics = list(syntax.diagnostics)
        if not syntax.ok:
            return VerificationReport(False, diagnostics=diagnostics)

        command = self._targeted_command(unique)
        if not command or self.process_runner is None:
            return VerificationReport(True, diagnostics=diagnostics, command=command)

        try:
            result = self.process_runner.run(command, timeout_seconds=120)
        except Exception as exc:
            diagnostics.append(
                GateDiagnostic("<project>", "verification.command", False, str(exc)[:1200])
            )
            return VerificationReport(False, diagnostics=diagnostics, command=command)

        output = (getattr(result, "stderr", "") or getattr(result, "stdout", "") or "").strip()
        return VerificationReport(
            getattr(result, "returncode", 1) == 0,
            diagnostics=diagnostics,
            command=command,
            command_returncode=int(getattr(result, "returncode", 1)),
            command_output=output,
        )
