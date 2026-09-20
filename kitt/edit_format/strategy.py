"""Model-aware editing strategy selection with observed-success feedback."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Any, Dict, Iterable, Literal, Mapping


EditStrategy = Literal["structured_symbol", "search_replace", "whole_file"]
EDIT_STRATEGIES: tuple[EditStrategy, ...] = (
    "structured_symbol",
    "search_replace",
    "whole_file",
)


@dataclass(frozen=True)
class EditStrategyHistory:
    attempts: int = 0
    successes: int = 0

    @property
    def success_rate(self) -> float:
        # Beta(1, 1) prior avoids overreacting to one attempt.
        return (self.successes + 1.0) / (self.attempts + 2.0)


class EditStrategyTracker:
    def __init__(self):
        self._lock = threading.RLock()
        self._history: Dict[EditStrategy, EditStrategyHistory] = {
            strategy: EditStrategyHistory() for strategy in EDIT_STRATEGIES
        }

    def record(self, strategy: EditStrategy, success: bool) -> None:
        if strategy not in self._history:
            return
        with self._lock:
            current = self._history[strategy]
            self._history[strategy] = EditStrategyHistory(
                attempts=current.attempts + 1,
                successes=current.successes + (1 if success else 0),
            )

    def snapshot(self) -> Dict[EditStrategy, EditStrategyHistory]:
        with self._lock:
            return dict(self._history)


@dataclass(frozen=True)
class EditStrategyDecision:
    strategy: EditStrategy
    scores: Mapping[EditStrategy, float]
    reasons: tuple[str, ...]


def strategy_for_tool_call(tool_name: str, args: Any) -> EditStrategy | None:
    name = str(tool_name or "")
    operation = ""
    if name == "kitt_runtime" and isinstance(args, dict):
        operation = str(args.get("operation") or "")
    elif name:
        operation = name

    if operation in {"repo.edit_symbol", "edit_symbol"}:
        return "structured_symbol"
    if operation in {"patch.apply", "apply_patch"}:
        return "search_replace"
    if operation in {"repo.write_file", "write_file"}:
        return "whole_file"
    return None


def edit_result_was_executed(result: Any) -> bool:
    if bool(getattr(result, "requires_approval", False)):
        return False
    if bool(getattr(result, "success", False)):
        return True
    error = str(getattr(result, "error", "") or "").casefold()
    if not error:
        return True
    non_execution_markers = (
        "execution denied by policyengine",
        "capability '",
        "is not granted",
        "requires approval",
        "approval grant",
        "outside workspace",
    )
    return not any(marker in error for marker in non_execution_markers)


class EditStrategySelector:
    """Choose an edit format without changing tool authority or execution semantics."""

    @staticmethod
    def _safe_target_states(
        root_path: str | Path,
        targets: Iterable[str],
    ) -> tuple[int, int]:
        root = Path(root_path).resolve()
        existing = 0
        missing = 0
        for raw in dict.fromkeys(str(item or "").strip() for item in targets):
            if not raw:
                continue
            candidate = (root / raw.lstrip("@")).resolve(strict=False)
            try:
                candidate.relative_to(root)
            except ValueError:
                continue
            if candidate.exists():
                existing += 1
            else:
                missing += 1
        return existing, missing

    def select(
        self,
        *,
        model_capabilities: Any,
        task: Any,
        prompt: str,
        explicit_files: Iterable[str],
        root_path: str | Path,
        history: Mapping[EditStrategy, EditStrategyHistory] | None = None,
    ) -> EditStrategyDecision:
        scores: Dict[EditStrategy, float] = {
            "structured_symbol": 0.45,
            "search_replace": 0.70,
            "whole_file": 0.25,
        }
        reasons: list[str] = []

        caps = model_capabilities
        if caps is not None:
            edit_score = max(0.0, min(1.0, float(getattr(caps, "code_edit_score", 0.5))))
            reasoning = max(0.0, min(1.0, float(getattr(caps, "reasoning_score", 0.5))))
            tool_reliability = max(
                0.0, min(1.0, float(getattr(caps, "tool_call_reliability", 0.5)))
            )
            scores["structured_symbol"] += 0.80 * (edit_score - 0.5)
            scores["structured_symbol"] += 0.40 * (reasoning - 0.5)
            scores["structured_symbol"] += 0.25 * (tool_reliability - 0.5)
            if int(getattr(caps, "input_context_limit", 0) or 0) >= 16_384:
                scores["structured_symbol"] += 0.15
            if str(getattr(caps, "tier", "")).lower() == "small":
                scores["search_replace"] += 0.25
                scores["structured_symbol"] -= 0.10
            if bool(getattr(caps, "is_local", False)):
                scores["search_replace"] += 0.10
            if edit_score >= 0.85:
                reasons.append("high model code-edit score")

        symbols = tuple(str(item) for item in getattr(task, "symbols", ()) if str(item))
        if symbols:
            scores["structured_symbol"] += 0.80
            reasons.append("task names concrete symbols")

        raw_intent = getattr(task, "intent", "")
        intent = str(getattr(raw_intent, "value", raw_intent) or "").upper()
        if intent in {"DEBUG", "REFACTOR"}:
            scores["structured_symbol"] += 0.25
        elif intent == "IMPLEMENT":
            scores["search_replace"] += 0.10

        task_paths = tuple(str(item) for item in getattr(task, "paths", ()) if str(item))
        existing, missing = self._safe_target_states(
            root_path,
            (*tuple(explicit_files or ()), *task_paths),
        )
        if missing:
            scores["whole_file"] += 0.90 + min(0.30, missing * 0.10)
            reasons.append("one or more target files do not exist yet")
        if existing and not symbols:
            scores["search_replace"] += 0.15

        prompt_lower = str(prompt or "").casefold()
        creation_terms = (
            "crie", "criar", "create", "generate", "gere", "novo arquivo",
            "new file", "scaffold",
        )
        if missing and any(term in prompt_lower for term in creation_terms):
            scores["whole_file"] += 0.30

        if history:
            for strategy in EDIT_STRATEGIES:
                stats = history.get(strategy)
                if stats is None or stats.attempts < 2:
                    continue
                confidence = min(1.0, stats.attempts / 6.0)
                adjustment = (stats.success_rate - 0.5) * 1.20 * confidence
                scores[strategy] += adjustment
                reasons.append(
                    f"{strategy} observed success {stats.successes}/{stats.attempts}"
                )

        tie_order: tuple[EditStrategy, ...] = (
            "search_replace",
            "structured_symbol",
            "whole_file",
        )
        selected = max(
            tie_order,
            key=lambda strategy: (scores[strategy], -tie_order.index(strategy)),
        )
        return EditStrategyDecision(
            strategy=selected,
            scores={key: round(value, 4) for key, value in scores.items()},
            reasons=tuple(reasons),
        )
