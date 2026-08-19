from __future__ import annotations

import ast
import datetime
import json
import math
import re
import sys
from typing import Any, Dict, List, Optional


FORBIDDEN_MODULES = {
    "os", "sys", "subprocess", "socket", "shutil", "importlib", "builtins",
    "posix", "nt", "ctypes", "threading", "multiprocessing", "signal",
    "inspect", "pickle", "urllib", "http", "requests", "aiohttp", "pathlib",
    "code", "codeop", "pty", "commands",
}


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    mod_root = name.split(".")[0]
    if mod_root in FORBIDDEN_MODULES:
        raise PermissionError(f"Import of forbidden module '{mod_root}' is blocked in executable skill")
    return __import__(name, globals, locals, fromlist, level)


SAFE_BUILTINS = {
    "__import__": _safe_import,
    "abs": abs,
    "all": all,
    "any": any,
    "bin": bin,
    "bool": bool,
    "bytes": bytes,
    "chr": chr,
    "dict": dict,
    "divmod": divmod,
    "enumerate": enumerate,
    "filter": filter,
    "float": float,
    "format": format,
    "frozenset": frozenset,
    "hash": hash,
    "hex": hex,
    "int": int,
    "isinstance": isinstance,
    "issubclass": issubclass,
    "iter": iter,
    "len": len,
    "list": list,
    "map": map,
    "max": max,
    "min": min,
    "next": next,
    "oct": oct,
    "ord": ord,
    "pow": pow,
    "print": lambda *args, **kwargs: None,  # Suppress direct stdout pollution
    "range": range,
    "repr": repr,
    "reversed": reversed,
    "round": round,
    "set": set,
    "slice": slice,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "type": type,
    "zip": zip,
    "None": None,
    "True": True,
    "False": False,
    "Exception": Exception,
    "ValueError": ValueError,
    "TypeError": TypeError,
    "KeyError": KeyError,
    "IndexError": IndexError,
    "RuntimeError": RuntimeError,
    "AttributeError": AttributeError,
    "PermissionError": PermissionError,
}


class SubprocessSkillContextProxy:
    """RPC proxy running inside isolated skill worker, delegating capabilities to parent process."""

    def __init__(self, declared_caps: List[str]):
        self.declared_caps = set(declared_caps)

    def _rpc(self, method: str, params: Dict[str, Any]) -> Any:
        req = {"type": "RPC_CALL", "method": method, "params": params}
        sys.stdout.write(json.dumps(req, ensure_ascii=False) + "\n")
        sys.stdout.flush()

        res_line = sys.stdin.readline()
        if not res_line:
            raise RuntimeError("Parent process closed IPC channel")

        res = json.loads(res_line)
        if not res.get("success", False):
            raise RuntimeError(res.get("error", "Unknown RPC error"))
        return res.get("data")

    def read_file(self, path: str, start_line: Optional[int] = None, end_line: Optional[int] = None) -> str:
        return self._rpc("repo.read", {"path": path, "start_line": start_line, "end_line": end_line})

    def write_file(self, path: str, content: str) -> bool:
        return self._rpc("repo.write", {"path": path, "content": content})

    def search_repo(self, query: str, max_results: int = 20) -> List[Dict[str, Any]]:
        return self._rpc("repo.search", {"query": query, "max_results": max_results})

    def run_process(self, command: str, timeout_seconds: int = 30) -> Dict[str, Any]:
        return self._rpc("process.run", {"command": command, "timeout_seconds": timeout_seconds})

    def spawn_child(self, name: str, task: str, allowed_paths: Optional[List[str]] = None) -> Any:
        return self._rpc("children.spawn", {"name": name, "task": task, "allowed_paths": allowed_paths or []})


def main() -> None:
    try:
        init_line = sys.stdin.readline()
        if not init_line:
            return

        payload = json.loads(init_line)
        source = payload.get("source", "")
        arguments = payload.get("arguments", {})
        capabilities = payload.get("capabilities", [])

        compiled = compile(source, filename="<skill_sandbox>", mode="exec")
        sandbox_globals = {
            "__builtins__": SAFE_BUILTINS,
            "json": json,
            "math": math,
            "re": re,
            "datetime": datetime,
        }
        sandbox_locals: Dict[str, Any] = {}

        exec(compiled, sandbox_globals, sandbox_locals)

        handler = sandbox_locals.get("execute") or sandbox_locals.get("main")
        if not callable(handler):
            sys.stdout.write(json.dumps({"type": "RESULT", "success": False, "error": "No execute/main function found"}) + "\n")
            sys.stdout.flush()
            return

        ctx = SubprocessSkillContextProxy(capabilities)
        res = handler(ctx, arguments)

        sys.stdout.write(json.dumps({"type": "RESULT", "success": True, "data": res}, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    except Exception as exc:
        sys.stdout.write(json.dumps({"type": "RESULT", "success": False, "error": str(exc)}, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
