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
    status: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "argv": list(self.argv),
            "passed": self.passed,
            "status": self.status or ("PASS" if self.passed else "FAIL"),
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
    status: str = ""
    checked_paths: list[str] = field(default_factory=list)
    workspace_verified: bool = False

    def __post_init__(self):
        if not self.status:
            self.status = "PASS" if self.ok else "FAIL"

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "checked_paths": list(self.checked_paths),
            "workspace_verified": self.workspace_verified,
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

    def verify(self, paths: Iterable[str], *, check_ids: Iterable[str] | None = None, cancellation=None) -> VerificationReport:
        unique = list(dict.fromkeys(str(path) for path in paths if path))[:64]
        required = set(check_ids or ())
        full = self._full_enabled() or bool(required)
        syntax = self.validator.validate_paths(unique)
        diagnostics = list(syntax.diagnostics)
        if not syntax.ok:
            return VerificationReport(
                False,
                diagnostics=diagnostics,
                full_verification=full,
            )

        plan = self._plan(unique, full)
        if required:
            plan = [step for step in plan if step.name in required]
            missing = required - {step.name for step in plan}
            if missing:
                return VerificationReport(False, diagnostics=diagnostics, full_verification=full, status="UNAVAILABLE",
                    command_output="Unavailable verification steps: " + ", ".join(sorted(missing)))
        if not plan:
            status = "SKIPPED" if not full and self._plan(unique, True) else "NOT_APPLICABLE"
            return VerificationReport(True, diagnostics=diagnostics, full_verification=full, status=status)
        if self.process_runner is None:
            return VerificationReport(False, diagnostics=diagnostics, command=plan[0].argv,
                full_verification=full, status="UNAVAILABLE", command_output="Verification executor is unavailable")

        step_results: list[VerificationStepResult] = []
        for step in plan:
            try:
                result = self.process_runner.run(
                    step.argv,
                    timeout_seconds=step.timeout_seconds,
                    **({"cancellation": cancellation} if cancellation is not None else {}),
                )
                returncode = int(getattr(result, "returncode", 1))
                timed_out = bool(getattr(result, "timed_out", False))
                cancelled = bool(getattr(result, "cancelled", False))
                passed = returncode == 0 and not timed_out and not cancelled
                stdout = str(getattr(result, "stdout", "") or "")
                stderr = str(getattr(result, "stderr", "") or "")
                output = (stderr or stdout or "").strip()[:4000]
                status = "TIMED_OUT" if timed_out else ("CANCELLED" if cancelled else ("PASS" if passed else "FAIL"))
            except Exception as exc:
                returncode = None
                passed = False
                status = "UNAVAILABLE"
                output = f"{type(exc).__name__}: {exc}"[:4000]

            step_result = VerificationStepResult(
                step.name,
                step.kind,
                list(step.argv),
                passed,
                returncode,
                output,
                status,
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
                    status=status,
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
            checked_paths=unique,
            workspace_verified=any(step.scope == "workspace" for step in plan),
        )
