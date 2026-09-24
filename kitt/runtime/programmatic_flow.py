"""Declarative programmatic tool calling with hidden intermediate results."""
from __future__ import annotations

import concurrent.futures
import copy
import json
import threading
from dataclasses import dataclass
from typing import Any


# Explicit allowlist: flow.execute compresses read-only work into fewer model
# round-trips, but it must never become a generic bypass around approvals,
# mutations, external tools, or potentially expensive security subprocesses.
READ_ONLY_FLOW_OPERATIONS = {
    "repo.read",
    "repo.search",
    "repo.inspect_symbol",
    "repo.read_symbol",
    "repo.references",
    "repo.context_map",
    "repo.definition",
    "repo.hover",
    "repo.references_semantic",
    "repo.diagnostics",
    "repo.call_hierarchy",
    "repo.outline",
    "repo.ast_search",
    "artifacts.read",
    "goal.inspect",
    "memory.query",
    "session.search",
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


def _references(value: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, str) and value.startswith("$"):
        step, _, _path = value[1:].partition(".")
        if step:
            refs.add(step)
    elif isinstance(value, list):
        for item in value:
            refs.update(_references(item))
    elif isinstance(value, dict):
        for item in value.values():
            refs.update(_references(item))
    return refs


def _project_item(value: Any, fields: list[str]) -> Any:
    if not isinstance(value, dict):
        return value
    return {field: value[field] for field in fields if field in value}


def _transform(value: Any, spec: Any) -> Any:
    """Apply bounded deterministic selection/projection/aggregation.

    This deliberately supports a tiny data algebra instead of arbitrary code:
    ``path`` selects a nested value, ``where`` performs exact dict matching,
    ``project`` keeps named keys, ``limit`` slices arrays, ``unique`` removes
    duplicate scalar/JSON values, and ``aggregate`` supports count/first/last.
    """
    if spec is None:
        return value
    if not isinstance(spec, dict):
        raise ValueError("transform must be an object")
    result = copy.deepcopy(value)

    path = str(spec.get("path") or "").strip()
    if path:
        result = _get_path(result, path)

    where = spec.get("where")
    if where is not None:
        if not isinstance(where, dict) or not isinstance(result, list):
            raise ValueError("transform.where requires an array and object predicate")
        result = [
            item for item in result
            if isinstance(item, dict) and all(item.get(k) == v for k, v in where.items())
        ]

    project = spec.get("project")
    if project is not None:
        if not isinstance(project, list) or not all(isinstance(x, str) for x in project):
            raise ValueError("transform.project must be an array of field names")
        fields = project[:32]
        if isinstance(result, list):
            result = [_project_item(item, fields) for item in result]
        else:
            result = _project_item(result, fields)

    if bool(spec.get("unique", False)) and isinstance(result, list):
        seen: set[str] = set()
        unique_items = []
        for item in result:
            key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
            if key not in seen:
                seen.add(key)
                unique_items.append(item)
        result = unique_items

    if "limit" in spec:
        try:
            limit = max(0, min(int(spec.get("limit", 0)), 1000))
        except (TypeError, ValueError) as exc:
            raise ValueError("transform.limit must be an integer") from exc
        if isinstance(result, list):
            result = result[:limit]

    aggregate = str(spec.get("aggregate") or "").strip().lower()
    if aggregate:
        if aggregate == "count":
            return len(result) if isinstance(result, (list, dict, str)) else int(result is not None)
        if aggregate == "first":
            return result[0] if isinstance(result, list) and result else None
        if aggregate == "last":
            return result[-1] if isinstance(result, list) and result else None
        raise ValueError("transform.aggregate must be one of: count, first, last")
    return result


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
            return SafeRuntimeResult(False, "flow.execute", error="steps must be a non-empty array")
        try:
            max_calls = max(1, min(int(arguments.get("max_tool_calls", 12)), 32))
        except (TypeError, ValueError):
            max_calls = 12
        try:
            max_tokens = max(64, min(int(arguments.get("max_tokens", 1200)), 8000))
        except (TypeError, ValueError):
            max_tokens = 1200
        parallel = bool(arguments.get("parallel", True))
        try:
            max_parallel = max(1, min(int(arguments.get("max_parallel", 4)), 8))
        except (TypeError, ValueError):
            max_parallel = 4

        values: dict[str, Any] = {}
        summaries: list[dict[str, Any]] = []
        intermediate_tokens = 0
        total_saved = 0
        call_count = 0
        parallel_batches = 0
        metrics_lock = threading.Lock()

        def run_call(step_id: str, operation: str, raw_args: dict[str, Any], scope: dict[str, Any]):
            nonlocal call_count, intermediate_tokens, total_saved
            if operation not in READ_ONLY_FLOW_OPERATIONS:
                raise PermissionError(
                    f"flow step '{step_id}' operation '{operation}' is not allowed. flow.execute is read-only by design."
                )
            resolved_args = _resolve(raw_args, scope)
            with metrics_lock:
                if call_count >= max_calls:
                    raise RuntimeError(f"flow exceeded max_tool_calls={max_calls}")
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
                raise PermissionError(f"flow step '{step_id}' unexpectedly requires approval")
            if not result.success:
                raise RuntimeError(f"flow step '{step_id}' failed: {result.error}")
            step_tokens = _json_tokens(result.data)
            summary = {
                "id": step_id,
                "operation": operation,
                "tokens": step_tokens,
                "duration_ms": round(float(result.duration_ms or 0.0), 2),
                "context_handles": list(result.context_handles or []),
            }
            with metrics_lock:
                intermediate_tokens += step_tokens
                total_saved += int(result.tokens_saved or 0)
                summaries.append(summary)
            return result.data

        normalized_steps: list[dict[str, Any]] = []
        step_ids: list[str] = []
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                return SafeRuntimeResult(
                    False, "flow.execute", error=f"step {index + 1} must be an object"
                )
            step_id = str(step.get("id") or f"step_{index + 1}")
            if not step_id or step_id in step_ids:
                return SafeRuntimeResult(
                    False,
                    "flow.execute",
                    error=f"step id must be unique: {step_id!r}",
                )
            raw_args = step.get("arguments", {})
            if not isinstance(raw_args, dict):
                return SafeRuntimeResult(
                    False,
                    "flow.execute",
                    error=f"step '{step_id}' arguments must be an object",
                )
            normalized_steps.append({**step, "id": step_id, "arguments": raw_args})
            step_ids.append(step_id)

        known_ids = set(step_ids)
        dependencies: dict[str, set[str]] = {}
        for step in normalized_steps:
            explicit = step.get("depends_on", [])
            if explicit is None:
                explicit = []
            if not isinstance(explicit, list) or not all(
                isinstance(item, str) for item in explicit
            ):
                return SafeRuntimeResult(
                    False,
                    "flow.execute",
                    error=f"step '{step['id']}' depends_on must be an array of step ids",
                )
            refs = _references(step.get("arguments", {}))
            if "foreach" in step:
                refs.update(_references(step["foreach"]))
                refs.discard(str(step.get("as") or "item"))
            refs.update(str(item) for item in explicit)
            refs.discard(step["id"])
            unknown = sorted(refs - known_ids)
            if unknown:
                return SafeRuntimeResult(
                    False,
                    "flow.execute",
                    error=(
                        f"step '{step['id']}' references unknown dependencies: "
                        + ", ".join(unknown)
                    ),
                )
            dependencies[step["id"]] = refs

        def execute_step(step: dict[str, Any], scope: dict[str, Any]) -> Any:
            step_id = step["id"]
            operation = str(step.get("operation") or "").strip()
            raw_args = step["arguments"]
            if "foreach" in step:
                collection = _resolve(step["foreach"], scope)
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
                    item_scope = {**scope, variable: item}
                    outputs.append(
                        run_call(
                            f"{step_id}[{item_index}]",
                            operation,
                            raw_args,
                            item_scope,
                        )
                    )
                return _transform(outputs, step.get("transform"))
            return _transform(
                run_call(step_id, operation, raw_args, scope),
                step.get("transform"),
            )

        pending = {step["id"]: step for step in normalized_steps}
        completed: set[str] = set()
        while pending:
            ready = [
                step
                for step in normalized_steps
                if step["id"] in pending
                and dependencies[step["id"]].issubset(completed)
            ]
            if not ready:
                blocked = ", ".join(sorted(pending))
                return SafeRuntimeResult(
                    False,
                    "flow.execute",
                    error=(
                        "flow dependency cycle or unresolved dependency among: "
                        f"{blocked}"
                    ),
                    data={"completed_steps": summaries},
                    tokens_saved=total_saved,
                )
            if not parallel:
                ready = ready[:1]

            scope = dict(values)
            try:
                if len(ready) == 1:
                    step_results = [
                        (ready[0]["id"], execute_step(ready[0], scope))
                    ]
                else:
                    parallel_batches += 1
                    workers = min(max_parallel, len(ready))
                    with concurrent.futures.ThreadPoolExecutor(
                        max_workers=workers,
                        thread_name_prefix="kitt-flow",
                    ) as pool:
                        futures = [
                            (step["id"], pool.submit(execute_step, step, scope))
                            for step in ready
                        ]
                        step_results = [
                            (step_id, future.result())
                            for step_id, future in futures
                        ]
                for step_id, value in step_results:
                    values[step_id] = value
                    completed.add(step_id)
                    pending.pop(step_id, None)
            except (
                KeyError,
                IndexError,
                ValueError,
                RuntimeError,
                PermissionError,
            ) as exc:
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
                    (ref[1:] if isinstance(ref, str) and ref.startswith("$") else str(i)): _resolve(ref, values)
                    for i, ref in enumerate(returns)
                }
            else:
                last_id = str(steps[-1].get("id") or f"step_{len(steps)}")
                output = values[last_id]
            output = _transform(output, arguments.get("transform"))
        except (KeyError, IndexError, ValueError) as exc:
            return SafeRuntimeResult(False, "flow.execute", error=f"flow return resolution failed: {exc}")

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
                "parallel": parallel,
                "parallel_batches": parallel_batches,
                "truncated": truncated,
            },
            tokens_saved=total_saved,
            metadata={
                "output_family": "programmatic_flow",
                "step_count": len(steps),
                "tool_call_count": call_count,
                "parallel": parallel,
                "parallel_batches": parallel_batches,
                "max_parallel": max_parallel,
                "raw_estimated_tokens": intermediate_tokens,
                "output_estimated_tokens": output_tokens,
                "tokens_saved": total_saved,
                "max_tokens": max_tokens,
            },
        )
