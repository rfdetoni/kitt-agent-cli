"""Evidence-first execution slicing for broad coding goals.

The user objective remains authoritative. This module only bounds the next model
action so application-sized requests enter the normal action/observation loop
without requiring the user to split the prompt manually.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from kitt.core.turn_command import TurnCommand
from kitt.domain.entities import ContextPlan, SemanticTask
from kitt.router.features import TaskFeatureExtractor


@dataclass(frozen=True)
class ExecutionSlice:
    phase: str
    objective: str
    milestones: tuple[str, ...]
    preferred_operations: tuple[str, ...]
    target_hints: tuple[str, ...] = ()
    reason: str = ""

    def render(self) -> str:
        operations = ", ".join(self.preferred_operations)
        targets = ", ".join(self.target_hints) if self.target_hints else "workspace root and project manifests"
        milestones = " -> ".join(self.milestones)
        return (
            "[KITT EXECUTION SLICE: DISCOVERY]\n"
            "The complete user objective remains authoritative, but the current model action is bounded.\n"
            f"Objective: {self.objective}\n"
            f"Host milestones: {milestones}\n"
            f"Evidence targets: {targets}\n"
            f"Preferred read-only operations: {operations}\n"
            "FIRST ACTION RULE: before any host tool result exists for this turn, return exactly one "
            "read-only repository tool call. Do not generate file contents, mutate the workspace, run "
            "a build, restate the full architecture, or return a final answer before evidence arrives. "
            "Prefer repo.read/repo.inspect_symbol for a concrete target; otherwise use repo.list or repo.search.\n"
            "AFTER EVIDENCE: advance only the next smallest evidence-backed milestone. Existing files "
            "should use repo.edit_symbol or patch.apply where practical; use repo.write_file for new files "
            "or intentional complete small-file replacements. Validate meaningful milestones instead of "
            "trying to finish the entire remaining goal in one model response."
        )


def _objective(task: SemanticTask, prompt: str) -> str:
    value = str(task.goal or prompt or "").replace("\x00", " ")
    return " ".join(value.split())[:1000] or "Complete the requested workspace change."


def _milestones(intent: str) -> tuple[str, ...]:
    if intent == "IMPLEMENT":
        return ("DISCOVERY", "FOUNDATION", "DOMAIN", "APPLICATION", "VERIFY")
    if intent == "DEBUG":
        return ("DISCOVERY", "REPRODUCE", "FIX", "VERIFY")
    return ("DISCOVERY", "CHANGE", "VERIFY")


def build_execution_slice(cmd: TurnCommand, task: SemanticTask, plan: ContextPlan) -> Optional[ExecutionSlice]:
    """Select a deterministic discovery bootstrap for a broad mutation turn."""
    if cmd.dry_run or str(cmd.mode or "").lower() in {"plan", "ask"}:
        return None
    intent = str(getattr(task, "intent", "") or "").upper()
    if not plan.enabled_tools or intent not in {"IMPLEMENT", "DEBUG", "REFACTOR"}:
        return None

    features = TaskFeatureExtractor.from_task(
        task, prompt=cmd.prompt, explicit_files=set(cmd.explicit_files or ())
    )
    requested_scope = (
        len(getattr(task, "actions", ()) or ())
        + len(getattr(task, "paths", ()) or ())
        + len(getattr(task, "symbols", ()) or ())
        + len(getattr(task, "technologies", ()) or ())
    )
    if not (
        features.complexity == "HIGH"
        or features.cross_module
        or features.estimated_files >= 4
        or features.prompt_tokens >= 240
        or requested_scope >= 5
    ):
        return None

    targets = tuple(dict.fromkeys([
        *(cmd.explicit_files or ()),
        *(getattr(task, "paths", ()) or ()),
    ]))[:6]
    preferred = (
        ("repo.read", "repo.inspect_symbol", "repo.list", "repo.search")
        if targets else ("repo.list", "repo.search", "repo.read")
    )
    return ExecutionSlice(
        phase="DISCOVERY",
        objective=_objective(task, cmd.prompt),
        milestones=_milestones(intent),
        preferred_operations=preferred,
        target_hints=targets,
        reason=(
            f"complexity={features.complexity}; files={features.estimated_files}; "
            f"scope={requested_scope}; prompt_tokens={features.prompt_tokens}"
        ),
    )
