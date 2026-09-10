from __future__ import annotations

from typing import Any

from kitt.integrations.semantic import SemanticCodeIntelligence
from kitt.runtime import core_runtime as _core
from kitt.security.capabilities import CAP_REPO_READ, CAP_REPO_SEARCH

RuntimeOperationSpec = _core.RuntimeOperationSpec
SafeRuntimeResult = _core.SafeRuntimeResult
OPERATION_SPECS = _core.OPERATION_SPECS

OPERATION_SPECS.update({
    "repo.definition": RuntimeOperationSpec("repo.definition", CAP_REPO_READ, "read_file"),
    "repo.hover": RuntimeOperationSpec("repo.hover", CAP_REPO_READ, "read_file"),
    "repo.references_semantic": RuntimeOperationSpec("repo.references_semantic", CAP_REPO_SEARCH, "search"),
    "repo.diagnostics": RuntimeOperationSpec("repo.diagnostics", CAP_REPO_READ, "read_file"),
    "repo.call_hierarchy": RuntimeOperationSpec("repo.call_hierarchy", CAP_REPO_SEARCH, "search"),
    "repo.outline": RuntimeOperationSpec("repo.outline", CAP_REPO_READ, "read_file"),
    "repo.ast_search": RuntimeOperationSpec("repo.ast_search", CAP_REPO_SEARCH, "search"),
    "security.scan": RuntimeOperationSpec("security.scan", CAP_REPO_SEARCH, "search"),
})


class SafeRuntime(_core.SafeRuntime):
    """SafeRuntime facade adding optional semantic/security adapters."""

    @property
    def semantic(self) -> SemanticCodeIntelligence:
        engine = getattr(self, "_semantic_code_intelligence", None)
        if engine is None:
            engine = SemanticCodeIntelligence(self.root)
            self._semantic_code_intelligence = engine
        return engine

    @staticmethod
    def _bounded_paths(args: dict[str, Any]) -> list[str]:
        raw = args.get("paths")
        if raw is None:
            path = str(args.get("path") or "").strip()
            return [path] if path else []
        if not isinstance(raw, (list, tuple)):
            raise ValueError("'paths' must be an array")
        return [str(path) for path in raw[:64] if str(path).strip()]

    @staticmethod
    def _assert_path_scope(security_context, paths: list[str]) -> None:
        if security_context is not None:
            for path in paths:
                security_context.assert_path_allowed(path)

    def _semantic_dispatch(self, op: str, args: dict[str, Any], security_context):
        path = str(args.get("path") or "").strip()
        line = max(1, int(args.get("line", 1) or 1))
        column = max(0, int(args.get("column", 0) or 0))
        positional = {"repo.definition", "repo.hover", "repo.references_semantic", "repo.diagnostics", "repo.call_hierarchy", "repo.outline"}
        if op in positional:
            if not path:
                raise ValueError(f"'{op}' requires path")
            self._assert_path_scope(security_context, [path])

        if op == "repo.definition":
            data = self.semantic.definition(path, line, column)
        elif op == "repo.hover":
            data = self.semantic.hover(path, line, column)
        elif op == "repo.references_semantic":
            data = self.semantic.references(path, line, column, limit=max(1, min(int(args.get("limit", 100)), 1000)))
        elif op == "repo.diagnostics":
            data = self.semantic.diagnostics(path)
        elif op == "repo.call_hierarchy":
            data = self.semantic.call_hierarchy(path, line, column)
        elif op == "repo.outline":
            data = self.semantic.outline(path)
        elif op == "repo.ast_search":
            pattern = str(args.get("pattern") or "")
            paths = self._bounded_paths(args) or ["."]
            self._assert_path_scope(security_context, paths)
            raw_globs = args.get("globs") or []
            if not isinstance(raw_globs, (list, tuple)):
                raise ValueError("'globs' must be an array")
            data = self.semantic.ast_search(
                pattern,
                paths=paths,
                language=(str(args.get("language")).strip() if args.get("language") else None),
                globs=[str(item) for item in raw_globs[:32]],
                limit=max(1, min(int(args.get("limit", 100)), 1000)),
                timeout_seconds=max(1.0, min(float(args.get("timeout_seconds", 15)), 120.0)),
            )
        elif op == "security.scan":
            paths = self._bounded_paths(args)
            if not paths:
                raise ValueError("'security.scan' requires one or more changed paths")
            self._assert_path_scope(security_context, paths)
            data = self.semantic.security_scan(
                paths,
                config_path=(str(args.get("config_path")).strip() if args.get("config_path") else None),
                max_findings=max(1, min(int(args.get("max_findings", 200)), 5000)),
                timeout_seconds=max(1.0, min(float(args.get("timeout_seconds", 30)), 600.0)),
            )
        else:
            raise ValueError(f"Unsupported semantic operation: {op}")
        return SafeRuntimeResult(True, op, data=data)

    def _dispatch(self, op, args, turn_id, origin, security_context, capabilities, grant, expected_approval_id):
        if op in {"repo.definition", "repo.hover", "repo.references_semantic", "repo.diagnostics", "repo.call_hierarchy", "repo.outline", "repo.ast_search", "security.scan"}:
            return self._semantic_dispatch(op, args, security_context)
        return super()._dispatch(op, args, turn_id, origin, security_context, capabilities, grant, expected_approval_id)


def __getattr__(name: str):
    try:
        return getattr(_core, name)
    except AttributeError as exc:
        raise AttributeError(name) from exc


__all__ = ["SafeRuntime", "SafeRuntimeResult", "RuntimeOperationSpec", "OPERATION_SPECS"]
