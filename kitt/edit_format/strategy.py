"""Model-aware editing strategy selection with observed-success feedback."""
from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import threading
from typing import Any, Dict, Iterable, Literal, Mapping


logger = logging.getLogger(__name__)

EditStrategy = Literal["structured_symbol", "search_replace", "whole_file"]
EDIT_STRATEGIES: tuple[EditStrategy, ...] = (
    "structured_symbol",
    "search_replace",
    "whole_file",
)
MIN_HISTORY_SAMPLES = 4.0


@dataclass(frozen=True)
class EditStrategyContext:
    workspace_id: str = ""
    provider: str = ""
    model: str = ""
    language: str = ""
    project_type: str = ""

    def normalized(self) -> "EditStrategyContext":
        return EditStrategyContext(
            workspace_id=str(self.workspace_id or "")[:128],
            provider=str(self.provider or "").strip().casefold()[:128],
            model=str(self.model or "").strip()[:256],
            language=str(self.language or "").strip().casefold()[:64],
            project_type=str(self.project_type or "").strip().casefold()[:64],
        )


@dataclass(frozen=True)
class EditStrategyHistory:
    attempts: float = 0.0
    successes: float = 0.0
    parse_failures: float = 0.0
    apply_failures: float = 0.0
    validation_failures: float = 0.0
    repair_required: float = 0.0
    rollbacks: float = 0.0
    files_changed: float = 0.0
    output_tokens: float = 0.0
    latency_ms: float = 0.0

    @property
    def success_rate(self) -> float:
        # Beta(1, 1) prior avoids overreacting to a small sample.
        return (self.successes + 1.0) / (self.attempts + 2.0)

    @property
    def repair_rate(self) -> float:
        return self.repair_required / self.attempts if self.attempts > 0 else 0.0

    @property
    def rollback_rate(self) -> float:
        return self.rollbacks / self.attempts if self.attempts > 0 else 0.0

    @property
    def validation_failure_rate(self) -> float:
        return self.validation_failures / self.attempts if self.attempts > 0 else 0.0

    @property
    def avg_output_tokens(self) -> float:
        return self.output_tokens / self.attempts if self.attempts > 0 else 0.0

    @property
    def avg_latency_ms(self) -> float:
        return self.latency_ms / self.attempts if self.attempts > 0 else 0.0

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "EditStrategyHistory":
        kwargs = {}
        for field_name in cls.__dataclass_fields__:
            try:
                kwargs[field_name] = max(0.0, float(value.get(field_name, 0.0) or 0.0))
            except (TypeError, ValueError):
                kwargs[field_name] = 0.0
        return cls(**kwargs)


@dataclass(frozen=True)
class EditStrategyObservation:
    success: bool
    failure_kind: str = ""
    repair_required: bool = False
    rollback: bool = False
    files_changed: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0


class EditStrategyTracker:
    """Thread-safe feedback accumulator with optional workspace persistence.

    Persistence is advisory only. A repository failure never blocks editing;
    the selector falls back to deterministic in-memory history.
    """

    def __init__(self, repository: Any = None):
        self._lock = threading.RLock()
        self._repository = repository
        self._history: Dict[tuple[str, ...], Dict[EditStrategy, EditStrategyHistory]] = {}

    @staticmethod
    def _key(context: EditStrategyContext | None) -> tuple[str, ...]:
        if context is None:
            return ("", "", "", "", "")
        item = context.normalized()
        return (
            item.workspace_id,
            item.provider,
            item.model,
            item.language,
            item.project_type,
        )

    def bind_repository(self, repository: Any) -> None:
        with self._lock:
            self._repository = repository

    def _empty(self) -> Dict[EditStrategy, EditStrategyHistory]:
        return {strategy: EditStrategyHistory() for strategy in EDIT_STRATEGIES}

    def _load(self, context: EditStrategyContext) -> Dict[EditStrategy, EditStrategyHistory]:
        key = self._key(context)
        with self._lock:
            cached = self._history.get(key)
            if cached is not None:
                return dict(cached)
            history = self._empty()
            repository = self._repository
            if repository is not None and context.workspace_id:
                try:
                    rows = repository.get_edit_strategy_feedback(
                        workspace_id=context.workspace_id,
                        provider=context.provider,
                        model=context.model,
                        language=context.language,
                        project_type=context.project_type,
                    )
                    for strategy, row in rows.items():
                        if strategy in history:
                            history[strategy] = EditStrategyHistory.from_mapping(row)
                except Exception as exc:
                    logger.debug("edit-strategy feedback load skipped: %s", exc)
            self._history[key] = history
            return dict(history)

    def record(
        self,
        strategy: EditStrategy,
        success: bool,
        *,
        context: EditStrategyContext | None = None,
        failure_kind: str = "",
        repair_required: bool = False,
        rollback: bool = False,
        files_changed: int = 0,
        output_tokens: int = 0,
        latency_ms: float = 0.0,
    ) -> None:
        if strategy not in EDIT_STRATEGIES:
            return
        observation = EditStrategyObservation(
            success=bool(success),
            failure_kind=str(failure_kind or ""),
            repair_required=bool(repair_required),
            rollback=bool(rollback),
            files_changed=max(0, int(files_changed or 0)),
            output_tokens=max(0, int(output_tokens or 0)),
            latency_ms=max(0.0, float(latency_ms or 0.0)),
        )
        key = self._key(context)
        ctx = context.normalized() if context is not None else None
        with self._lock:
            current_map = self._history.setdefault(key, self._empty())
            current = current_map[strategy]
            next_history = EditStrategyHistory(
                attempts=current.attempts + 1.0,
                successes=current.successes + (1.0 if observation.success else 0.0),
                parse_failures=current.parse_failures + (
                    1.0 if observation.failure_kind == "parse_failure" else 0.0
                ),
                apply_failures=current.apply_failures + (
                    1.0 if observation.failure_kind == "apply_failure" else 0.0
                ),
                validation_failures=current.validation_failures + (
                    1.0 if observation.failure_kind == "validation_failure" else 0.0
                ),
                repair_required=current.repair_required + (
                    1.0 if observation.repair_required else 0.0
                ),
                rollbacks=current.rollbacks + (1.0 if observation.rollback else 0.0),
                files_changed=current.files_changed + observation.files_changed,
                output_tokens=current.output_tokens + observation.output_tokens,
                latency_ms=current.latency_ms + observation.latency_ms,
            )
            current_map[strategy] = next_history

            repository = self._repository
            if repository is not None and ctx is not None and ctx.workspace_id:
                try:
                    row = repository.record_edit_strategy_feedback(
                        workspace_id=ctx.workspace_id,
                        provider=ctx.provider,
                        model=ctx.model,
                        language=ctx.language,
                        project_type=ctx.project_type,
                        strategy=strategy,
                        success=observation.success,
                        failure_kind=observation.failure_kind,
                        repair_required=observation.repair_required,
                        rollback=observation.rollback,
                        files_changed=observation.files_changed,
                        output_tokens=observation.output_tokens,
                        latency_ms=observation.latency_ms,
                    )
                    if isinstance(row, Mapping):
                        current_map[strategy] = EditStrategyHistory.from_mapping(row)
                except Exception as exc:
                    logger.debug("edit-strategy feedback persistence skipped: %s", exc)

    def snapshot(
        self,
        context: EditStrategyContext | None = None,
    ) -> Dict[EditStrategy, EditStrategyHistory]:
        if context is not None:
            return self._load(context)
        with self._lock:
            return dict(self._history.get(self._key(None), self._empty()))


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


def infer_edit_strategy_context(
    *,
    workspace_id: str,
    provider: str,
    model: str,
    task: Any,
    explicit_files: Iterable[str],
    root_path: str | Path,
) -> EditStrategyContext:
    technologies = [str(item).casefold() for item in getattr(task, "technologies", ()) if str(item)]
    paths = [
        str(item).lstrip("@")
        for item in (*tuple(explicit_files or ()), *tuple(getattr(task, "paths", ()) or ()))
        if str(item)
    ]
    suffix_to_language = {
        ".py": "python",
        ".java": "java",
        ".kt": "kotlin",
        ".kts": "kotlin",
        ".js": "javascript",
        ".mjs": "javascript",
        ".cjs": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".rs": "rust",
        ".go": "go",
        ".cs": "csharp",
        ".rb": "ruby",
        ".php": "php",
        ".sql": "sql",
    }
    language = ""
    for technology in technologies:
        for candidate in (
            "java", "kotlin", "python", "typescript", "javascript",
            "rust", "go", "csharp", "ruby", "php", "sql",
        ):
            if candidate in technology:
                language = candidate
                break
        if language:
            break
    if not language:
        discovered = {
            suffix_to_language.get(Path(path).suffix.casefold(), "")
            for path in paths
        }
        discovered.discard("")
        if len(discovered) == 1:
            language = next(iter(discovered))
        elif len(discovered) > 1:
            language = "mixed"

    root = Path(root_path).resolve()
    markers = {
        "java": ("pom.xml", "build.gradle", "build.gradle.kts"),
        "node": ("package.json", "pnpm-lock.yaml", "yarn.lock"),
        "python": ("pyproject.toml", "requirements.txt", "setup.py"),
        "rust": ("Cargo.toml",),
        "go": ("go.mod",),
    }
    detected = [
        kind for kind, filenames in markers.items()
        if any((root / filename).exists() for filename in filenames)
    ]
    if len(detected) > 1:
        project_type = "polyglot"
    elif detected:
        project_type = detected[0]
    else:
        project_type = language or "generic"

    return EditStrategyContext(
        workspace_id=workspace_id,
        provider=provider,
        model=model,
        language=language or "generic",
        project_type=project_type,
    ).normalized()


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
                if stats is None or stats.attempts < MIN_HISTORY_SAMPLES:
                    continue
                confidence = min(1.0, stats.attempts / 12.0)
                quality = (
                    stats.success_rate
                    - (0.35 * stats.repair_rate)
                    - (0.55 * stats.rollback_rate)
                    - (0.30 * stats.validation_failure_rate)
                )
                adjustment = (quality - 0.5) * 1.20 * confidence
                if stats.avg_output_tokens > 1800:
                    adjustment -= 0.05 * confidence
                if stats.avg_latency_ms > 2500:
                    adjustment -= 0.05 * confidence
                scores[strategy] += adjustment
                reasons.append(
                    f"{strategy} observed success {stats.successes:.1f}/{stats.attempts:.1f}"
                    f" repair={stats.repair_rate:.2f} rollback={stats.rollback_rate:.2f}"
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
