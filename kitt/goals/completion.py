from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


COMPLETION_REPORT_PREFIX = "KITT_COMPLETION_REPORT:"
COMPLETION_STATE_KEY_PREFIX = "goal.completion:"


def completion_state_key(goal_id: str) -> str:
    return f"{COMPLETION_STATE_KEY_PREFIX}{goal_id}"


def _normalize(value: str) -> str:
    return " ".join(str(value or "").strip().split()).casefold()


def _bounded(value: str, limit: int = 4000) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


@dataclass(frozen=True)
class CompletionCheck:
    kind: str
    name: str
    passed: bool
    evidence: str = ""


@dataclass(frozen=True)
class CompletionVerification:
    success: bool
    score: float
    checks: List[CompletionCheck] = field(default_factory=list)
    feedback: str = ""
    failure_signature: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "score": self.score,
            "checks": [asdict(check) for check in self.checks],
            "feedback": self.feedback,
            "failure_signature": self.failure_signature,
        }


class AutonomousCompletionEngine:
    """Verify goal completion and produce bounded corrective feedback.

    Deterministic quality gates are authoritative. Semantic success criteria use
    a strict machine-readable completion report emitted by the executing model.
    The engine itself never performs workspace mutations; corrections still flow
    through the next canonical TurnProcessor turn and its policy/approval path.
    """

    def __init__(
        self,
        *,
        gate_runner=None,
        gate_result_recorder=None,
        gate_authorizer=None,
        stagnation_threshold: int = 2,
        score_epsilon: float = 0.01,
    ):
        self.gate_runner = gate_runner
        self.gate_result_recorder = gate_result_recorder
        self.gate_authorizer = gate_authorizer
        self.stagnation_threshold = max(2, int(stagnation_threshold))
        self.score_epsilon = max(0.0, float(score_epsilon))

    @staticmethod
    def has_contract(goal) -> bool:
        return bool(getattr(goal, "success_criteria", None) or getattr(goal, "gates", None))

    @staticmethod
    def _extract_report(response: str) -> Optional[Dict[str, Any]]:
        text = str(response or "")
        idx = text.rfind(COMPLETION_REPORT_PREFIX)
        if idx < 0:
            return None
        raw = text[idx + len(COMPLETION_REPORT_PREFIX) :].lstrip()
        if raw.startswith("```"):
            first_newline = raw.find("\n")
            if first_newline >= 0:
                raw = raw[first_newline + 1 :]
        try:
            value, _ = json.JSONDecoder().raw_decode(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def build_execution_prompt(
        self,
        goal,
        base_prompt: str,
        previous_state: Optional[Dict[str, Any]] = None,
    ) -> str:
        state = previous_state if isinstance(previous_state, dict) else {}
        if not self.has_contract(goal) and not str(state.get("feedback") or "").strip():
            return base_prompt

        criteria = list(getattr(goal, "success_criteria", None) or [])
        gates = list(getattr(goal, "gates", None) or [])
        blocks = [
            str(base_prompt or "").strip(),
            "",
            "[KITT AUTONOMOUS COMPLETION CONTRACT]",
            "Do not consider the task complete merely because code was written.",
            "Inspect the current workspace state, validate the implementation, and correct any problem you can find before finishing this turn.",
            "Never fabricate test results or evidence. Deterministic quality gates are authoritative over your own assessment.",
            "After objective verification passes, KITT may run an independent adversarial senior code review. Required reviewer findings are completion gaps, not optional suggestions.",
        ]

        if criteria:
            blocks.append("Success criteria that MUST all be satisfied:")
            blocks.extend(f"- {criterion}" for criterion in criteria)
        if gates:
            blocks.append("Quality gates that KITT will rerun after this turn:")
            for gate in gates:
                argv = " ".join(getattr(gate, "argv", None) or [])
                blocks.append(f"- {getattr(gate, 'name', 'QualityGate')}: {argv}")

        feedback = str(state.get("feedback") or "").strip()
        if feedback:
            blocks.extend(
                [
                    "",
                    "Previous verification failed. Correct these remaining gaps; do not redo parts that are already valid:",
                    _bounded(feedback, 8000),
                ]
            )
        if bool(state.get("stagnated")):
            blocks.extend(
                [
                    "",
                    "STAGNATION DETECTED: the last verification failure repeated without meaningful score improvement.",
                    "Use a materially different diagnostic or implementation strategy this turn instead of repeating the previous patch/approach.",
                ]
            )

        if criteria:
            example = {
                "status": "SUCCEEDED",
                "criteria": [
                    {
                        "criterion": criterion,
                        "satisfied": True,
                        "evidence": "specific command/test/file/runtime evidence",
                    }
                    for criterion in criteria
                ],
                "summary": "concise completion summary",
            }
            blocks.extend(
                [
                    "",
                    "Before your final response ends, emit exactly one machine-readable completion line using the exact criterion text above:",
                    f"{COMPLETION_REPORT_PREFIX} {json.dumps(example, ensure_ascii=False, separators=(',', ':'))}",
                    "Use status INCOMPLETE and satisfied=false for any criterion that is not actually proven; KITT will iterate automatically.",
                ]
            )

        return "\n".join(blocks).strip()

    def _run_gate(self, gate) -> CompletionCheck:
        name = str(getattr(gate, "name", "QualityGate") or "QualityGate")
        argv = list(getattr(gate, "argv", None) or [])
        timeout = int(getattr(gate, "timeout_seconds", 120) or 120)
        if self.gate_runner is None:
            return CompletionCheck(
                "gate",
                name,
                False,
                "Quality gate runner unavailable; completion is fail-closed.",
            )

        if self.gate_authorizer is not None:
            try:
                decision = str(self.gate_authorizer(argv) or "DENY").upper()
            except Exception as exc:
                return CompletionCheck(
                    "gate",
                    name,
                    False,
                    f"Quality gate authorization failed: {type(exc).__name__}: {exc}",
                )
            if decision != "ALLOW":
                return CompletionCheck(
                    "gate",
                    name,
                    False,
                    f"Quality gate was not executed because PolicyEngine returned {decision}. "
                    "Use the autonomous policy only when unattended process execution is intended; "
                    "DENY decisions are never overridden.",
                )

        try:
            result = self.gate_runner.run(argv, timeout_seconds=timeout)
            returncode = int(getattr(result, "returncode", -1))
            timed_out = bool(getattr(result, "timed_out", False))
            cancelled = bool(getattr(result, "cancelled", False))
            passed = returncode == 0 and not timed_out and not cancelled
            status = "PASSED" if passed else "FAILED"
            if self.gate_result_recorder is not None:
                self.gate_result_recorder(
                    getattr(gate, "id", ""),
                    exit_code=returncode,
                    status=status,
                )
            stdout = _bounded(getattr(result, "stdout", ""), 3000)
            stderr = _bounded(getattr(result, "stderr", ""), 3000)
            evidence = (
                f"command={argv!r}; exit_code={returncode}; "
                f"timed_out={timed_out}; cancelled={cancelled}"
            )
            if stdout:
                evidence += f"\nstdout:\n{stdout}"
            if stderr:
                evidence += f"\nstderr:\n{stderr}"
            return CompletionCheck("gate", name, passed, evidence)
        except Exception as exc:
            if self.gate_result_recorder is not None:
                try:
                    self.gate_result_recorder(
                        getattr(gate, "id", ""),
                        exit_code=-1,
                        status="FAILED",
                    )
                except Exception:
                    pass
            return CompletionCheck(
                "gate",
                name,
                False,
                f"Quality gate execution error: {type(exc).__name__}: {exc}",
            )

    @staticmethod
    def _criterion_checks(criteria: List[str], report: Optional[Dict[str, Any]]) -> List[CompletionCheck]:
        if not criteria:
            return []
        if not report:
            return [
                CompletionCheck(
                    "criterion",
                    criterion,
                    False,
                    "Missing or invalid KITT_COMPLETION_REPORT.",
                )
                for criterion in criteria
            ]

        report_status = str(report.get("status") or "").strip().upper()
        entries = report.get("criteria")
        if not isinstance(entries, list):
            entries = []

        checks: List[CompletionCheck] = []
        for criterion in criteria:
            normalized = _normalize(criterion)
            match = None
            for item in entries:
                if not isinstance(item, dict):
                    continue
                if _normalize(item.get("criterion", "")) == normalized:
                    match = item
                    break

            if match is None:
                checks.append(
                    CompletionCheck(
                        "criterion",
                        criterion,
                        False,
                        "Criterion missing from completion report.",
                    )
                )
                continue

            evidence = str(match.get("evidence") or "").strip()
            satisfied = match.get("satisfied") is True
            passed = report_status == "SUCCEEDED" and satisfied and bool(evidence)
            if not passed and not evidence:
                evidence = "No evidence supplied."
            elif report_status != "SUCCEEDED":
                evidence = f"report_status={report_status or 'MISSING'}; {evidence}".strip()
            checks.append(CompletionCheck("criterion", criterion, passed, _bounded(evidence)))
        return checks

    def verify(self, goal, response: str) -> CompletionVerification:
        if not self.has_contract(goal):
            return CompletionVerification(True, 1.0, [], "", "")

        checks: List[CompletionCheck] = []
        for gate in list(getattr(goal, "gates", None) or []):
            checks.append(self._run_gate(gate))

        criteria = list(getattr(goal, "success_criteria", None) or [])
        report = self._extract_report(response) if criteria else None
        checks.extend(self._criterion_checks(criteria, report))

        total = len(checks)
        passed_count = sum(1 for check in checks if check.passed)
        success = total == 0 or passed_count == total
        score = 1.0 if total == 0 else passed_count / total

        failed = [check for check in checks if not check.passed]
        feedback = ""
        signature = ""
        if failed:
            feedback_lines = ["Autonomous completion verification failed:"]
            signature_parts = []
            for check in failed:
                feedback_lines.append(
                    f"- [{check.kind}] {check.name}: {_bounded(check.evidence, 3500)}"
                )
                signature_parts.append(f"{check.kind}:{_normalize(check.name)}")
            feedback = "\n".join(feedback_lines)
            signature = hashlib.sha256("\n".join(signature_parts).encode("utf-8")).hexdigest()

        return CompletionVerification(success, score, checks, feedback, signature)

    def include_adversarial_review(
        self,
        verification: CompletionVerification,
        review: Any,
    ) -> CompletionVerification:
        """Merge an independent code review as an additional authoritative check."""
        if not bool(getattr(review, "applicable", False)):
            return verification

        approved = bool(getattr(review, "approved", False))
        status = str(getattr(review, "status", "REVIEW") or "REVIEW")
        review_feedback = str(getattr(review, "feedback", "") or "").strip()
        review_summary = str(getattr(review, "summary", "") or "").strip()
        approval_evidence = str(getattr(review, "approval_evidence", "") or "").strip()
        evidence = approval_evidence or review_feedback or review_summary or status
        review_check = CompletionCheck(
            "review",
            "Adversarial code review",
            approved,
            _bounded(evidence, 12000),
        )
        checks = [*verification.checks, review_check]
        total = len(checks)
        passed_count = sum(1 for check in checks if check.passed)
        success = verification.success and approved
        score = 1.0 if total == 0 else passed_count / total

        if success:
            return CompletionVerification(True, score, checks, "", "")

        feedback_parts = [part for part in (verification.feedback, review_feedback) if part]
        feedback = "\n\n".join(feedback_parts)
        signature_parts = [
            part
            for part in (
                verification.failure_signature,
                str(getattr(review, "failure_signature", "") or ""),
            )
            if part
        ]
        signature = (
            hashlib.sha256("\n".join(signature_parts).encode("utf-8")).hexdigest()
            if signature_parts
            else ""
        )
        return CompletionVerification(False, score, checks, feedback, signature)

    def next_state(
        self,
        previous_state: Optional[Dict[str, Any]],
        verification: CompletionVerification,
    ) -> Dict[str, Any]:
        previous = previous_state if isinstance(previous_state, dict) else {}
        iteration = int(previous.get("iteration", 0) or 0) + 1
        previous_signature = str(previous.get("failure_signature") or "")
        previous_score = float(previous.get("score", 0.0) or 0.0)
        same_failure = bool(
            verification.failure_signature
            and verification.failure_signature == previous_signature
        )
        streak = int(previous.get("stagnation_streak", 0) or 0) + 1 if same_failure else 1
        improved = verification.score > previous_score + self.score_epsilon
        stagnated = bool(same_failure and streak >= self.stagnation_threshold and not improved)
        return {
            "iteration": iteration,
            "score": verification.score,
            "feedback": verification.feedback,
            "failure_signature": verification.failure_signature,
            "stagnation_streak": streak,
            "stagnated": stagnated,
        }
