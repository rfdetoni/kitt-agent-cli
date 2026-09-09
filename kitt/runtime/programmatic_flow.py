"""Declarative programmatic tool calling with hidden intermediate results."""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any


READ_ONLY_FLOW_OPERATIONS = {
    "repo.read",
    "repo.search",
    "repo.inspect_symbol",
    "repo.read_symbol",
    "repo.references",
    "repo.context_map",
    "artifacts.read",
    "goal.inspect",
    "memory.query",
    "handles.resolve",
    "state.get",
    "state.list",
}


@dataclass(frozen=True)
class FlowLimits:
    max_steps: int = 12
    max_steps_hard: int = 32
    max_tokens: int = 1200


def _json_tokens(value: Any) -> int:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")
    ).encode("utf-8")
    return (len(raw) + 3) // 4


def _get_path(value: Any, path: str) -> Any:
    current = value
    if not path:
        return current
    for segment in path.split("."):
        if isinstance(current, list):
            current = current[int(segment)]
        elif isinstance(current, dict):
            current = current[segment]
        else:
            raise KeyError(path)
    return current


def _resolve(value: Any, results: dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("$"):
        reference = value[1:]
        step, dot, path = reference.partition(".")
        if step not in results:
            raise KeyError(f"Unknown flow step reference: {step}")
        return copy.deepcopy(_get_path(results[step], path if dot else ""))
    if isinstance(value, list):
        return [_resolve(item, results) for item in value]
    if isinstance(value, dict):
        return {key: _resolve(item, results) for key, item in value.items()}
    return value


def _bounded(value: Any, max_tokens: int) -> tuple[Any, bool]:
    if _json_tokens(value) <= max_tokens:
        return value, False
    if isinstance(value, list):
        kept = []
        for item in value:
            candidate = kept + [item]
            if kept and _json_tokens(candidate) > max_tokens:
                break
            kept = candidate
            if _json_tokens(kept) >= max_tokens:
                break
        return kept, len(kept) < len(value)
    if isinstance(value, dict):
        kept: dict[str, Any] = {}
        for key, item in value.items():
            candidate = {**kept, key: item}
            if kept and _json_tokens(candidate) > max_tokens:
                break
            kept[key] = item
            if _json_tokens(kept) >= max_tokens:
                break
        return kept, len(kept) < len(value)
    text = str(value)
    max_bytes = max_tokens * 4
    raw = text.encode("utf-8")[:max_bytes]
    while raw:
        try:
            return raw.decode("utf-8"), True
        except UnicodeDecodeError:
            raw = raw[:-1]
    return "", True


class ProgrammaticToolFlow:
    """Execute a bounded read-only tool graph while hiding intermediate payloads."""

    def __init__(self, runtime):
        self.runtime = runtime

    def execute(
        self,
        arguments: dict[str, Any],
        *,
        turn_id: str,
        origin: str,
        capabilities: set[str],
        security_context,
    ):
        from kitt.runtime.safe_runtime import SafeRuntimeResult

        steps = arguments.get("steps", [])
        if not isinstance(steps, list) or not steps:
            return SafeRuntimeResult(
                False, "flow.execute", error="steps must be a non-empty array"
            )
        try:
            max_calls = max(1, min(int(arguments.get("max_tool_calls", 12)), 32))
        except (TypeError, ValueError):
            max_calls = 12
        try:
            max_tokens = max(64, min(int(arguments.get("max_tokens", 1200)), 8000))
        except (TypeError, ValueError):
            max_tokens = 1200

        values: dict[str, Any] = {}
        summaries: list[dict[str, Any]] = []
        intermediate_tokens = 0
        total_saved = 0
        call_count = 0

        def run_call(
            step_id: str,
            operation: str,
            raw_args: dict[str, Any],
            scope: dict[str, Any],
        ):
            nonlocal call_count, intermediate_tokens, total_saved
            if operation not in READ_ONLY_FLOW_OPERATIONS:
                raise PermissionError(
                    f"flow step '{step_id}' operation '{operation}' is not "
                    "allowed. flow.execute is read-only by design."
                )
            if call_count >= max_calls:
                raise RuntimeError(
                    f"flow exceeded max_tool_calls={max_calls}"
                )
            resolved_args = _resolve(raw_args, scope)
            call_count += 1
            result = self.runtime.execute(
                operation,
                resolved_args,
                turn_id=turn_id,
                origin=origin,
                effective_capabilities=capabilities,
                security_context=security_context,
            )
            if result.requires_approval:
                raise PermissionError(
                    f"flow step '{step_id}' unexpectedly requires approval"
                )
            if not result.success:
                raise RuntimeError(
                    f"flow step '{step_id}' failed: {result.error}"
                )
            step_tokens = _json_tokens(result.data)
            intermediate_tokens += step_tokens
            total_saved += int(result.tokens_saved or 0)
            summaries.append(
                {
                    "id": step_id,
                    "operation": operation,
                    "tokens": step_tokens,
                    "duration_ms": round(float(result.duration_ms or 0.0), 2),
                    "context_handles": list(result.context_handles or []),
                }
            )
            return result.data

        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                return SafeRuntimeResult(
                    False, "flow.execute", error=f"step {index + 1} must be an object"
                )
            step_id = str(step.get("id") or f"step_{index + 1}")
            operation = str(step.get("operation") or "").strip()
            raw_args = step.get("arguments", {})
            if not isinstance(raw_args, dict):
                return SafeRuntimeResult(
                    False,
                    "flow.execute",
                    error=f"step '{step_id}' arguments must be an object",
                )

            try:
                if "foreach" in step:
                    collection = _resolve(step["foreach"], values)
                    if not isinstance(collection, list):
                        raise ValueError(
                            f"step '{step_id}' foreach must resolve to an array"
                        )
                    try:
                        foreach_limit = max(
                            1, min(int(step.get("limit", 5) or 5), 16)
                        )
                    except (TypeError, ValueError):
                        foreach_limit = 5
                    variable = str(step.get("as") or "item")
                    outputs = []
                    for item_index, item in enumerate(collection[:foreach_limit]):
                        scope = {**values, variable: item}
                        outputs.append(
                            run_call(
                                f"{step_id}[{item_index}]",
                                operation,
                                raw_args,
                                scope,
                            )
                        )
                    values[step_id] = outputs
                else:
                    values[step_id] = run_call(
                        step_id, operation, raw_args, values
                    )
            except (KeyError, IndexError, ValueError, RuntimeError, PermissionError) as exc:
                return SafeRuntimeResult(
                    False,
                    "flow.execute",
                    error=str(exc),
                    data={"completed_steps": summaries},
                    tokens_saved=total_saved,
                )

        returns = arguments.get("return", [])
        try:
            if isinstance(returns, str):
                output: Any = _resolve(returns, values)
            elif isinstance(returns, list) and returns:
                output = {
                    (
                        ref[1:]
                        if isinstance(ref, str) and ref.startswith("$")
                        else str(i)
                    ): _resolve(ref, values)
                    for i, ref in enumerate(returns)
                }
            else:
                last_id = str(steps[-1].get("id") or f"step_{len(steps)}")
                output = values[last_id]
        except (KeyError, IndexError, ValueError) as exc:
            return SafeRuntimeResult(
                False, "flow.execute", error=f"flow return resolution failed: {exc}"
            )

        bounded, truncated = _bounded(output, max_tokens)
        output_tokens = _json_tokens(bounded)
        avoided = max(0, intermediate_tokens - output_tokens)
        total_saved += avoided

        return SafeRuntimeResult(
            True,
            "flow.execute",
            data={
                "result": bounded,
                "steps": summaries,
                "tool_calls": call_count,
                "intermediate_payloads_hidden": True,
                "truncated": truncated,
            },
            tokens_saved=total_saved,
            metadata={
                "output_family": "programmatic_flow",
                "step_count": len(steps),
                "tool_call_count": call_count,
                "raw_estimated_tokens": intermediate_tokens,
                "output_estimated_tokens": output_tokens,
                "tokens_saved": total_saved,
                "max_tokens": max_tokens,
            },
        )
