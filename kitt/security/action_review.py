"""Deterministic pre-execution review for actions that would otherwise require approval."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from kitt.security.risk_budget import is_automatic_origin


ActionReviewDecision = Literal[
    "DENY",
    "SANDBOX_ALLOW",
    "AUTO_REVIEW",
    "ASK_USER",
    "ALLOW",
]


@dataclass(frozen=True)
class ActionReviewResult:
    decision: ActionReviewDecision
    tool_name: str
    reason: str
    policy_decision: str
    command_classification: str = ""
    sandbox_strong: bool = False
    network_requested: bool = False
    control_plane_elevation: bool = False

    @property
    def allowed(self) -> bool:
        return self.decision in {"SANDBOX_ALLOW", "AUTO_REVIEW", "ALLOW"}

    def to_dict(self) -> dict:
        return asdict(self)


class ActionReviewBroker:
    """Fail-closed reviewer that never grants capabilities.

    The first production rule is intentionally narrow: an automatic
    run_command that policy still marks ASK may proceed only when the
    command classifier already proved it read-only, network is not requested,
    no control-plane elevation exists, and a strong OS sandbox is available.
    """

    def review(
        self,
        *,
        tool_name: str,
        permission: str,
        origin: str,
        auto_review_enabled: bool,
        command_classification: str = "",
        sandbox_strong: bool = False,
        network_requested: bool = False,
        control_plane_elevation: bool = False,
    ) -> ActionReviewResult:
        policy_decision = str(permission or "ASK").upper()
        command_decision = str(command_classification or "").upper()

        def result(decision: ActionReviewDecision, reason: str) -> ActionReviewResult:
            return ActionReviewResult(
                decision=decision,
                tool_name=str(tool_name or ""),
                reason=reason,
                policy_decision=policy_decision,
                command_classification=command_decision,
                sandbox_strong=bool(sandbox_strong),
                network_requested=bool(network_requested),
                control_plane_elevation=bool(control_plane_elevation),
            )

        if policy_decision == "DENY":
            return result("DENY", "hard policy denial cannot be reviewed away")
        if policy_decision == "ALLOW":
            return result("ALLOW", "policy already allows the action")
        if policy_decision != "ASK":
            return result("ASK_USER", "unknown policy decision fails closed")
        if not auto_review_enabled:
            return result("ASK_USER", "auto-review is disabled by autonomy policy")
        if not is_automatic_origin(origin):
            return result("ASK_USER", "explicit/user-origin actions do not need auto-review")
        if control_plane_elevation:
            return result("ASK_USER", "control-plane authority requires explicit approval")
        if network_requested:
            return result("ASK_USER", "network access requires explicit authority")
        if tool_name != "run_command":
            return result("ASK_USER", "no auto-review rule exists for this tool")
        if command_decision == "DENY":
            return result("DENY", "command classifier denied the argv")
        if command_decision != "ALLOW":
            return result("ASK_USER", "command is not demonstrably read-only")
        if not sandbox_strong:
            return result("ASK_USER", "strong OS sandbox is unavailable")
        return result(
            "SANDBOX_ALLOW",
            "read-only argv verified and strong no-network sandbox available",
        )
