from __future__ import annotations

from dataclasses import dataclass

from kitt.goals.completion import CompletionCheck
from kitt.security.capabilities import CAP_PROCESS_RUN
from kitt.validation.orchestrator import VerificationOrchestrator


@dataclass(frozen=True)
class ContractCheckResult:
    checks: list[CompletionCheck]
    evidence: dict
    evidence_text: str
    paths: list[str]


class ContractCheckRunner:
    """Resolve model-proposed check ids to host-owned verification steps."""

    def __init__(self, runtime):
        self.runtime = runtime

    def run(self, goal, item, paths: list[str]) -> ContractCheckResult:
        unique_paths = list(dict.fromkeys(str(path) for path in paths if str(path).strip()))[:64]

        def authorize(step):
            if CAP_PROCESS_RUN not in set(goal.capabilities or []):
                return "DENY"
            return self.runtime.policy.evaluate_tool(
                "run_command",
                {"argv": list(step.argv)},
                origin="SCHEDULE",
                conversation_id=goal.conversation_id,
                workspace_id=self.runtime.workspace_id,
            )

        orchestrator = VerificationOrchestrator(
            self.runtime.canonical_root,
            self.runtime.registry.process_runner,
            authorize_step=authorize,
        )
        report = orchestrator.verify(
            unique_paths,
            check_ids=list(item.check_ids),
            force_full=item.kind == "final",
        )

        checks: list[CompletionCheck] = []
        for diagnostic in report.diagnostics:
            checks.append(
                CompletionCheck(
                    "deterministic",
                    f"{diagnostic.validator}:{diagnostic.path}",
                    bool(diagnostic.ok),
                    str(diagnostic.message or diagnostic.validator),
                )
            )
        for step in report.steps:
            checks.append(
                CompletionCheck(
                    "deterministic",
                    step.name,
                    bool(step.passed),
                    (
                        f"status={step.status}; returncode={step.returncode}; "
                        f"argv={step.argv!r}\n{step.output}"
                    )[:6000],
                )
            )

        if not checks and (item.check_ids or unique_paths):
            checks.append(
                CompletionCheck(
                    "deterministic",
                    "Host verification",
                    bool(report.ok),
                    (
                        f"status={report.status}; checked_paths={unique_paths!r}; "
                        f"{report.command_output or report.failure_message()}"
                    )[:6000],
                )
            )
        elif not report.ok and all(check.passed for check in checks):
            checks.append(
                CompletionCheck(
                    "deterministic",
                    "Host verification",
                    False,
                    report.failure_message()[:6000],
                )
            )

        evidence = report.as_dict()
        evidence_text = (
            f"status={report.status}; ok={report.ok}; full={report.full_verification}; "
            f"checked_paths={unique_paths!r}\n"
            + (report.failure_message() if not report.ok else report.command_output)
        ).strip()
        return ContractCheckResult(checks, evidence, evidence_text[:12000], unique_paths)
