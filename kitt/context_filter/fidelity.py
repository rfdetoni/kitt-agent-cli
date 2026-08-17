"""Deterministic fidelity validator for compiled SemanticTask IR."""
from __future__ import annotations

from typing import Tuple
from kitt.domain.entities import SemanticTask
from kitt.context_filter.deterministic_extractor import DeterministicExtractor

IR_ONLY_CONFIDENCE_THRESHOLD = 0.90
ORIGINAL_FALLBACK_CONFIDENCE_THRESHOLD = 0.70


from kitt.domain.entities import SemanticConfidence


def validate_semantic_fidelity(
    original_prompt: str,
    task: SemanticTask,
    extractor: DeterministicExtractor | None = None
) -> Tuple[bool, str]:
    """Deterministically validates if SemanticTask faithfully represents original user prompt."""
    ext = extractor or DeterministicExtractor()

    # 1. Intent validation: cannot be UNKNOWN
    intent_score = 1.0 if task.intent != "UNKNOWN" else 0.0
    if task.intent == "UNKNOWN":
        task.semantic_confidence = SemanticConfidence(intent=0.0, goal=0.0, targets=0.0, constraints=0.0, actions=0.0, overall=0.0)
        return False, "Intent is UNKNOWN"

    # 2. Non-empty goal for non-trivial tasks
    goal_score = 1.0 if task.goal.strip() or ext.is_trivial_prompt(original_prompt) else 0.0
    if not ext.is_trivial_prompt(original_prompt) and not task.goal.strip():
        task.semantic_confidence = SemanticConfidence(intent=intent_score, goal=0.0, targets=0.0, constraints=0.0, actions=0.0, overall=0.0)
        return False, "Goal is empty for non-trivial prompt"

    # 3. Linguistic tasks require verbatim original prompt
    if ext.is_linguistic_task(original_prompt):
        task.semantic_confidence = SemanticConfidence(intent=intent_score, goal=1.0, targets=1.0, constraints=1.0, actions=1.0, overall=0.5)
        return False, "Linguistic task requires original prompt"

    # 4. Explicit paths detected in original prompt must be preserved in task.paths
    explicit_paths = ext.extract_paths(original_prompt)
    missing_paths = [p for p in explicit_paths if p not in task.paths]
    targets_score = 0.0 if missing_paths else 1.0
    for p in missing_paths:
        task.semantic_confidence = SemanticConfidence(intent=intent_score, goal=goal_score, targets=0.0, constraints=1.0, actions=1.0, overall=0.0)
        return False, f"Explicit path '{p}' missing from task.paths"

    # 5. High-confidence symbols detected in original prompt must be preserved
    explicit_symbols = ext.extract_symbols(original_prompt)
    task_targets_text = " ".join(task.symbols + task.paths + [task.goal] + task.actions)
    for s in explicit_symbols:
        if s not in task_targets_text:
            task.semantic_confidence = SemanticConfidence(intent=intent_score, goal=goal_score, targets=0.5, constraints=1.0, actions=1.0, overall=0.5)
            return False, f"Explicit symbol '{s}' missing from compiled task"

    # 6. Negative / mandatory constraints detected in original prompt must be preserved
    deterministic_constraints = ext.extract_constraints(original_prompt)
    constraints_score = 1.0
    for dc in deterministic_constraints:
        matched = any(
            dc.text.lower() in tc.text.lower() or tc.text.lower() in dc.text.lower()
            for tc in task.constraints
        )
        if not matched:
            constraints_score = 0.0
            task.semantic_confidence = SemanticConfidence(intent=intent_score, goal=goal_score, targets=targets_score, constraints=0.0, actions=1.0, overall=0.0)
            return False, f"Negative constraint '{dc.text}' missing from task constraints"

    # 7. Preserved diagnostics: stack traces / exceptions must not be lost
    diagnostics = ext.extract_diagnostics(original_prompt)
    for diag in diagnostics:
        if diag.lower() not in task_targets_text.lower():
            task.semantic_confidence = SemanticConfidence(intent=intent_score, goal=goal_score, targets=0.7, constraints=constraints_score, actions=0.7, overall=0.6)
            return False, f"Diagnostic text '{diag}' missing from compiled task"

    actions_score = 1.0 if task.actions else 0.9
    overall = min(intent_score, goal_score, targets_score, constraints_score) * 0.8 + (actions_score * 0.2)
    task.semantic_confidence = SemanticConfidence(
        intent=intent_score,
        goal=goal_score,
        targets=targets_score,
        constraints=constraints_score,
        actions=actions_score,
        overall=overall
    )
    task.confidence = overall
    return True, "Fidelity validation passed"
