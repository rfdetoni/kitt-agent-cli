from __future__ import annotations

import ast
import json
import math
import re
import sys
from typing import Any, Dict, List, Optional

ALLOWED_MODULES = {"json", "math", "re"}
FORBIDDEN_ATTRIBUTES = {
    "__subclasses__", "__bases__", "__globals__", "__code__", "__closure__",
    "__mro__", "__import__", "__builtins__", "__dict__", "__class__",
}


def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    root = name.split(".")[0]
    if root not in ALLOWED_MODULES:
        raise PermissionError(f"Import '{root}' is not allowed in executable skill")
    return __import__(name, globals, locals, fromlist, level)


SAFE_BUILTINS = {
    "__import__": _safe_import,
    "abs": abs, "all": all, "any": any, "bool": bool, "bytes": bytes,
    "chr": chr, "dict": dict, "divmod": divmod, "enumerate": enumerate,
    "filter": filter, "float": float, "format": format, "frozenset": frozenset,
    "hash": hash, "hex": hex, "int": int, "isinstance": isinstance,
    "iter": iter, "len": len, "list": list, "map": map, "max": max, "min": min,
    "next": next, "oct": oct, "ord": ord, "pow": pow, "range": range,
    "repr": repr, "reversed": reversed, "round": round, "set": set,
    "slice": slice, "sorted": sorted, "str": str, "sum": sum, "tuple": tuple,
    "zip": zip, "Exception": Exception, "ValueError": ValueError,
    "TypeError": TypeError, "KeyError": KeyError, "IndexError": IndexError,
    "RuntimeError": RuntimeError, "PermissionError": PermissionError,
    "None": None, "True": True, "False": False,
}


def _validate_source(source: str) -> None:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for name in names:
                root = name.split(".")[0]
                if root not in ALLOWED_MODULES:
                    raise PermissionError(f"Import '{root}' is not allowed")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in {"eval", "exec", "open", "compile", "__import__", "globals", "locals", "vars", "getattr", "setattr", "delattr"}:
                raise PermissionError(f"Call '{node.func.id}' is not allowed")
        elif isinstance(node, ast.Attribute) and (node.attr.startswith("_") or node.attr in FORBIDDEN_ATTRIBUTES):
            raise PermissionError(f"Private/dangerous attribute '{node.attr}' is not allowed")


def _emit(payload: dict) -> None:
    raw = json.dumps(payload, ensure_ascii=False)
    if len(raw.encode("utf-8")) > 1_000_000:
        raw = json.dumps({"type": "RESULT", "success": False, "error": "Skill output exceeds 1 MB limit"})
    sys.stdout.write(raw + "\n")
    sys.stdout.flush()


class SubprocessSkillContextProxy:
    def __init__(self, declared_caps: List[str]):
        self.declared_caps = frozenset(declared_caps)

    def _rpc(self, method: str, params: Dict[str, Any]) -> Any:
        sys.stdout.write(json.dumps({"type": "RPC_CALL", "method": method, "params": params}) + "\n")
        sys.stdout.flush()
        line = sys.stdin.readline()
        if not line:
            raise RuntimeError("Capability broker closed")
        res = json.loads(line)
        if not res.get("success"):
            if res.get("requires_approval"):
                raise PermissionError("Operation requires external approval")
            raise RuntimeError(res.get("error", "RPC failed"))
        return res.get("data")

    def read_file(self, path, start_line=1, end_line=100):
        return self._rpc("repo.read", {"path": path, "start_line": start_line, "end_line": end_line})

    def search_repo(self, pattern, regex=False):
        return self._rpc("repo.search", {"pattern": pattern, "regex": regex})

    def apply_patch(self, patch):
        return self._rpc("patch.apply", {"patch": patch})

    def run_process(self, command):
        return self._rpc("process.run", {"command": command})

    def spawn_child(self, name, task, allowed_paths=None):
        return self._rpc("children.spawn", {"name": name, "task": task, "allowed_paths": allowed_paths or []})


def main():
    try:
        init = json.loads(sys.stdin.readline())
        source = init.get("source", "")
        args = init.get("arguments", {})
        caps = init.get("capabilities", [])

        _validate_source(source)
        compiled = compile(source, "<skill_sandbox>", "exec")
        g = {"__builtins__": SAFE_BUILTINS, "json": json, "math": math, "re": re}
        loc: Dict[str, Any] = {}
        exec(compiled, g, loc)
        handler = loc.get("execute") or loc.get("main")
        if not callable(handler):
            raise RuntimeError("No execute/main function found")
        result = handler(SubprocessSkillContextProxy(caps), args)
        _emit({"type": "RESULT", "success": True, "data": result})
    except BaseException as exc:
        _emit({"type": "RESULT", "success": False, "error": str(exc)})


if __name__ == "__main__":
    main()
