"""Unified, bounded verification for agent edits.

Cheap syntax/structure checks are always authoritative. Heavier project checks
are planned per language and run only when KITT's persistent full-verification
flag is enabled from the UI, except for cheap targeted tests.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from kitt.settings.runtime_flags import verification_full_enabled
from kitt.tools.build_detector import BuildDetector, VerificationStep
from kitt.validation.contract import VerificationContractManager
from kitt.validation.post_edit import GateDiagnostic, PostEditValidator


@dataclass
class VerificationStepResult:
    name: str
    kind: str
    argv: list[str]
    passed: bool
    returncode: int | None = None
    output: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "argv": list(self.argv),
            "passed": self.passed,
            "returncode": self.returncode,
            "output": self.output[:4000],
        }


@dataclass
class VerificationReport:
    ok: bool
    diagnostics: list[GateDiagnostic] = field(default_factory=list)
    command: list[str] | None = None
    command_returncode: int | None = None
    command_output: str = ""
    full_verification: bool = False
    steps: list[VerificationStepResult] = field(default_factory=list)

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
            "full_verification": self.full_verification,
            "steps": [step.as_dict() for step in self.steps],
        }

    def failure_message(self) -> str:
        parts = [
            f"{item.path}: {item.validator}: {item.message or 'failed'}"
            for item in self.diagnostics
            if not item.ok
        ]
        for step in self.steps:
            if not step.passed:
                parts.append(
                    f"{step.name} failed ({step.returncode}): {step.output[:2400]}"
                )
        return "\n".join(parts) or "verification failed"


class VerificationOrchestrator:
    """Run cheapest strong checks first, then a bounded workspace-aware plan."""

    def __init__(
        self,
        root: str | Path,
        process_runner=None,
        *,
        full_enabled: bool | Callable[[], bool] | None = None,
    ):
        self.root = Path(root).resolve()
        self.process_runner = process_runner
        self.validator = PostEditValidator(self.root, process_runner)
        self.detector = BuildDetector(str(self.root))
        self.contracts = VerificationContractManager(self.root)
        self.full_enabled = full_enabled

    def _full_enabled(self) -> bool:
        if callable(self.full_enabled):
            return bool(self.full_enabled())
        if self.full_enabled is not None:
            return bool(self.full_enabled)
        return verification_full_enabled(False)

    def _plan(self, paths: list[str], full: bool) -> list[VerificationStep]:
        planned = self.detector.plan_verification(paths, full=full)
        return self.contracts.apply_plan(planned)

    def verify(self, paths: Iterable[str]) -> VerificationReport:
        unique = list(dict.fromkeys(str(path) for path in paths if path))[:64]
        full = self._full_enabled()
        syntax = self.validator.validate_paths(unique)
        diagnostics = list(syntax.diagnostics)
        if not syntax.ok:
            return VerificationReport(
                False,
                diagnostics=diagnostics,
                full_verification=full,
            )

        plan = self._plan(unique, full)
        if not plan or self.process_runner is None:
            return VerificationReport(
                True,
                diagnostics=diagnostics,
                command=plan[0].argv if plan else None,
                full_verification=full,
            )

        step_results: list[VerificationStepResult] = []
        for step in plan:
            try:
                result = self.process_runner.run(
                    step.argv,
                    timeout_seconds=step.timeout_seconds,
                )
                returncode = int(getattr(result, "returncode", 1))
                timed_out = bool(getattr(result, "timed_out", False))
                cancelled = bool(getattr(result, "cancelled", False))
                passed = returncode == 0 and not timed_out and not cancelled
                stdout = str(getattr(result, "stdout", "") or "")
                stderr = str(getattr(result, "stderr", "") or "")
                output = (stderr or stdout or "").strip()[:4000]
            except Exception as exc:
                returncode = None
                passed = False
                output = f"{type(exc).__name__}: {exc}"[:4000]

            step_result = VerificationStepResult(
                step.name,
                step.kind,
                list(step.argv),
                passed,
                returncode,
                output,
            )
            step_results.append(step_result)
            if not passed:
                diagnostics.append(
                    GateDiagnostic(
                        "<project>",
                        f"verification.{step.kind}",
                        False,
                        output[:1200],
                    )
                )
                return VerificationReport(
                    False,
                    diagnostics=diagnostics,
                    command=list(step.argv),
                    command_returncode=returncode,
                    command_output=output,
                    full_verification=full,
                    steps=step_results,
                )

        last = step_results[-1] if step_results else None
        return VerificationReport(
            True,
            diagnostics=diagnostics,
            command=list(last.argv) if last else None,
            command_returncode=last.returncode if last else None,
            command_output=last.output if last else "",
            full_verification=full,
            steps=step_results,
        )
