"""Subprocess entrypoint for the safe Python interpreter."""

from __future__ import annotations

import json
import sys
from pathlib import Path

# ``python -I`` deliberately removes the script directory from sys.path.  Add
# only this trusted, fixed directory so the worker can import its interpreter;
# no workspace or user-controlled path is exposed.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from safe_python_runtime import (
    SafePythonError,
    SafeRuntimeLimits,
    execute_safe_python,
)


MAX_REQUEST_BYTES = 128 * 1024
MAX_RESPONSE_BYTES = 64 * 1024


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        print(json.dumps({"success": False, "error": "Worker request exceeds 128 KiB."}))
        return 2

    try:
        request = json.loads(raw.decode("utf-8"))
        limits_data = request.get("limits", {})
        limits = SafeRuntimeLimits(
            max_steps=int(limits_data.get("max_steps", 50_000)),
            max_ast_nodes=int(limits_data.get("max_ast_nodes", 4_000)),
            max_collection_items=int(limits_data.get("max_collection_items", 10_000)),
            max_output_chars=int(limits_data.get("max_output_chars", 32_768)),
            max_value_bytes=int(limits_data.get("max_value_bytes", 8 * 1024 * 1024)),
            timeout_seconds=float(limits_data.get("timeout_seconds", 2.0)),
        )
        result = execute_safe_python(
            code=request.get("code", ""),
            inputs=request.get("inputs", {}),
            result_var=request.get("result_var", "_result"),
            limits=limits,
        )
        response = {
            "success": True,
            "stdout": result.stdout,
            "result": result.result,
            "steps": result.steps,
            "truncated": result.truncated,
        }
        encoded = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_RESPONSE_BYTES:
            print(json.dumps({"success": False, "error": "Safe Python result exceeds 64 KiB."}))
            return 1
        print(encoded)
        return 0
    except (SafePythonError, ValueError, TypeError, KeyError, IndexError, ZeroDivisionError) as exc:
        print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    except Exception as exc:
        # Do not expose a traceback or worker internals to model context.
        print(json.dumps({"success": False, "error": f"Safe Python worker failed: {type(exc).__name__}"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
