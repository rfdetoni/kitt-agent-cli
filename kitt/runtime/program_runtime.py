"""Bounded read-only program IR for compressing multi-step model/tool interaction."""
from __future__ import annotations

import json
from typing import Any

from kitt.runtime.programmatic_flow import (
    READ_ONLY_FLOW_OPERATIONS,
    _bounded,
    _get_path,
    _json_tokens,
)


PROGRAM_OPERATIONS = frozenset(READ_ONLY_FLOW_OPERATIONS)


def _resolve(value: Any, scope: dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("$"):
        root, dot, path = value[1:].partition(".")
        if root not in scope:
            raise KeyError(f"Unknown program value: {root}")
        current = scope[root]
        return _get_path(current, path if dot else "")
    if isinstance(value, list):
        return [_resolve(item, scope) for item in value]
    if isinstance(value, dict):
        return {key: _resolve(item, scope) for key, item in value.items()}
    return value


def _condition(spec: Any, scope: dict[str, Any]) -> bool:
    if isinstance(spec, bool):
        return spec
    if not isinstance(spec, dict):
        return bool(_resolve(spec, scope))
    if "exists" in spec:
        try:
            value = _resolve(spec["exists"], scope)
        except (KeyError, IndexError):
            return False
        return value not in (None, "", [], {}, False)
    left = _resolve(spec.get("left"), scope)
    right = _resolve(spec.get("right"), scope)
    op = str(spec.get("op") or "eq").lower()
    if op == "eq":
        return left == right
    if op == "ne":
        return left != right
    if op == "contains":
        try:
            return right in left
        except TypeError:
            return False
    if op == "in":
        try:
            return left in right
        except TypeError:
            return False
    if op == "gt":
        return left > right
    if op == "gte":
        return left >= right
    if op == "lt":
        return left < right
    if op == "lte":
        return left <= right
    raise ValueError(f"unsupported program condition: {op}")


class BoundedProgramRuntime:
    """Interpret a tiny data/control IR; never eval/exec model supplied code."""

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

        program = arguments.get("program")
        if not isinstance(program, list) or not program:
            return SafeRuntimeResult(False, "program.execute", error="program must be a non-empty array")
        try:
            max_statements = max(1, min(int(arguments.get("max_statements", 64)), 256))
            max_calls = max(1, min(int(arguments.get("max_tool_calls", 24)), 64))
            max_tokens = max(64, min(int(arguments.get("max_tokens", 1600)), 8000))
        except (TypeError, ValueError):
            return SafeRuntimeResult(False, "program.execute", error="program limits must be integers")

        scope: dict[str, Any] = {}
        metrics = {"statements": 0, "tool_calls": 0, "intermediate_tokens": 0, "tokens_saved": 0}
        returned = {"set": False, "value": None}

        def run_statements(statements: list[Any], local_scope: dict[str, Any], depth: int = 0):
            if depth > 8:
                raise RuntimeError("program nesting exceeds depth 8")
            for raw in statements:
                if returned["set"]:
                    return
                if not isinstance(raw, dict):
                    raise ValueError("each program statement must be an object")
                metrics["statements"] += 1
                if metrics["statements"] > max_statements:
                    raise RuntimeError(f"program exceeded max_statements={max_statements}")

                if "call" in raw:
                    operation = str(raw.get("call") or "").strip()
                    if operation not in PROGRAM_OPERATIONS:
                        raise PermissionError(
                            f"program operation '{operation}' is not allowed; program.execute is read-only"
                        )
                    metrics["tool_calls"] += 1
                    if metrics["tool_calls"] > max_calls:
                        raise RuntimeError(f"program exceeded max_tool_calls={max_calls}")
                    resolved_args = _resolve(raw.get("arguments", {}), local_scope)
                    if not isinstance(resolved_args, dict):
                        raise ValueError("program call arguments must resolve to an object")
                    result = self.runtime.execute(
                        operation,
                        resolved_args,
                        turn_id=turn_id,
                        origin=origin,
                        effective_capabilities=capabilities,
                        security_context=security_context,
                    )
                    if result.requires_approval:
                        raise PermissionError(f"program operation '{operation}' unexpectedly requires approval")
                    if not result.success:
                        raise RuntimeError(f"program operation '{operation}' failed: {result.error}")
                    metrics["intermediate_tokens"] += _json_tokens(result.data)
                    metrics["tokens_saved"] += int(result.tokens_saved or 0)
                    save = str(raw.get("save") or "").strip()
                    collect = str(raw.get("collect") or "").strip()
                    if save:
                        local_scope[save] = result.data
                    if collect:
                        bucket = local_scope.setdefault(collect, [])
                        if not isinstance(bucket, list):
                            raise ValueError(f"collect target '{collect}' is not a list")
                        bucket.append(result.data)
                    continue

                if "set" in raw:
                    name = str(raw.get("set") or "").strip()
                    if not name:
                        raise ValueError("set requires a variable name")
                    local_scope[name] = _resolve(raw.get("value"), local_scope)
                    continue

                if "for_each" in raw:
                    collection = _resolve(raw.get("for_each"), local_scope)
                    if not isinstance(collection, list):
                        raise ValueError("for_each must resolve to an array")
                    variable = str(raw.get("as") or "item").strip() or "item"
                    limit = max(0, min(int(raw.get("limit", 16) or 16), 32))
                    body = raw.get("do", [])
                    if not isinstance(body, list):
                        raise ValueError("for_each.do must be an array")
                    for item in collection[:limit]:
                        local_scope[variable] = item
                        run_statements(body, local_scope, depth + 1)
                        if returned["set"]:
                            break
                    local_scope.pop(variable, None)
                    continue

                if "if" in raw:
                    branch = raw.get("then", []) if _condition(raw.get("if"), local_scope) else raw.get("else", [])
                    if not isinstance(branch, list):
                        raise ValueError("if branches must be arrays")
                    run_statements(branch, local_scope, depth + 1)
                    continue

                if "return" in raw:
                    returned["value"] = _resolve(raw.get("return"), local_scope)
                    returned["set"] = True
                    return

                raise ValueError("program statement requires call, set, for_each, if, or return")

        try:
            run_statements(program, scope)
            value = returned["value"] if returned["set"] else scope
            bounded, truncated = _bounded(value, max_tokens)
        except (KeyError, IndexError, TypeError, ValueError, RuntimeError, PermissionError) as exc:
            return SafeRuntimeResult(
                False,
                "program.execute",
                error=str(exc),
                data={"statements": metrics["statements"], "tool_calls": metrics["tool_calls"]},
                tokens_saved=int(metrics["tokens_saved"]),
            )

        output_tokens = _json_tokens(bounded)
        avoided = max(0, int(metrics["intermediate_tokens"]) - output_tokens)
        saved = int(metrics["tokens_saved"]) + avoided
        return SafeRuntimeResult(
            True,
            "program.execute",
            data={
                "result": bounded,
                "tool_calls": metrics["tool_calls"],
                "statements": metrics["statements"],
                "intermediate_payloads_hidden": True,
                "truncated": truncated,
            },
            tokens_saved=saved,
            metadata={
                "output_family": "bounded_program",
                "tool_call_count": metrics["tool_calls"],
                "statement_count": metrics["statements"],
                "raw_estimated_tokens": metrics["intermediate_tokens"],
                "output_estimated_tokens": output_tokens,
                "tokens_saved": saved,
                "max_tokens": max_tokens,
            },
        )
