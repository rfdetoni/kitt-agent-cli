from __future__ import annotations

from typing import Any

from kitt.integrations.semantic import SemanticCodeIntelligence
from kitt.runtime import core_runtime as _core
from kitt.runtime.search_fallback import full_scan_search
from kitt.security.capabilities import CAP_REPO_READ, CAP_REPO_SEARCH, CAP_REPO_WRITE
from kitt.security.workspace_fs import WorkspaceFileSystem
from kitt.security.workspace_mutations import delete_path, list_entries, move_path

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
    "repo.list": RuntimeOperationSpec("repo.list", CAP_REPO_READ, "list_files"),
    "repo.write_file": RuntimeOperationSpec(
        "repo.write_file",
        CAP_REPO_WRITE,
        "write_file",
        sensitive=True,
        resume_tool_name="write_file",
    ),
    "repo.move": RuntimeOperationSpec(
        "repo.move", CAP_REPO_WRITE, "write_file", sensitive=True
    ),
    "repo.rename": RuntimeOperationSpec(
        "repo.rename", CAP_REPO_WRITE, "write_file", sensitive=True
    ),
    "repo.delete": RuntimeOperationSpec(
        "repo.delete", CAP_REPO_WRITE, "write_file", sensitive=True
    ),
    "security.scan": RuntimeOperationSpec("security.scan", CAP_REPO_SEARCH, "search"),
})


class SafeRuntime(_core.SafeRuntime):
    """SafeRuntime facade adding optional semantic/security and filesystem adapters."""

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

    def _op_repo_search(self, args, turn_id, origin, security_context):
        result = super()._op_repo_search(args, turn_id, origin, security_context)
        if not result.success or str((result.metadata or {}).get("method", "")) != "scanner":
            return result
        allowed = (
            (lambda path: security_context.allows_path(path))
            if security_context is not None
            else None
        )
        data = full_scan_search(self.root, args, path_allowed=allowed)
        return SafeRuntimeResult(
            True,
            "repo.search",
            data=data,
            tokens_saved=max(0, int(data.get("omitted_matches", 0)) * 8),
            metadata={
                "backend": "scanner",
                "method": "scanner_full",
                "complete_file_scan": True,
                "max_tokens": int(args.get("max_tokens", args.get("token_budget", 1200)) or 1200),
            },
        )

    def _op_repo_list(self, args: dict[str, Any], security_context):
        rel = str(args.get("path") or ".")
        limit = max(1, min(int(args.get("limit", 100) or 100), 500))
        fs = WorkspaceFileSystem(self.root)
        relative = fs.relative(rel)
        if security_context is not None:
            if not security_context.allows_path(relative) and not security_context.is_ancestor_of_allowed_path(relative):
                raise PermissionError(f"Path '{relative}' is outside the principal path scope")
        entries = list_entries(fs, relative, limit=min(500, limit + 1))
        if security_context is not None and security_context.is_path_scoped:
            entries = [
                entry
                for entry in entries
                if security_context.allows_path(str(entry["path"]))
                or security_context.is_ancestor_of_allowed_path(str(entry["path"]))
            ]
        truncated = len(entries) > limit
        entries = entries[:limit]
        return SafeRuntimeResult(
            True,
            "repo.list",
            data={"entries": entries, "truncated": truncated, "path": relative},
            metadata={"method": "workspace_fs", "output_family": "listing"},
        )

    def _op_repo_move(self, op: str, args: dict[str, Any], turn_id: str, security_context):
        source = str(args.get("source") or args.get("path") or "").strip()
        destination = str(args.get("destination") or args.get("target") or "").strip()
        if not source or not destination:
            raise ValueError(f"'{op}' requires source and destination")
        self._assert_path_scope(security_context, [source, destination])
        fs = WorkspaceFileSystem(self.root)
        data = move_path(
            fs,
            source,
            destination,
            overwrite=bool(args.get("overwrite", False)),
            expected_sha256=(str(args.get("expected_content_hash")).strip() if args.get("expected_content_hash") else None),
            create_parents=bool(args.get("create_parents", True)),
        )
        source_rel = str(data["source"])
        destination_rel = str(data["destination"])
        if self.registry is not None:
            self.registry._refresh_index([source_rel, destination_rel])
            self.registry.record_changed_paths(
                conversation_id=self.conversation_id,
                turn_id=turn_id,
                changed=[source_rel, destination_rel],
                kind=op,
            )
        self.retrieval_guard.invalidate()
        return SafeRuntimeResult(True, op, data=data, metadata={"changed_paths": [source_rel, destination_rel]})

    def _op_repo_delete(self, args: dict[str, Any], turn_id: str, security_context):
        path = str(args.get("path") or "").strip()
        if not path:
            raise ValueError("'repo.delete' requires path")
        self._assert_path_scope(security_context, [path])
        fs = WorkspaceFileSystem(self.root)
        data = delete_path(
            fs,
            path,
            recursive=bool(args.get("recursive", False)),
            expected_sha256=(str(args.get("expected_content_hash")).strip() if args.get("expected_content_hash") else None),
        )
        changed = [str(data["path"])]
        if self.registry is not None:
            self.registry._refresh_index(changed)
            self.registry.record_changed_paths(
                conversation_id=self.conversation_id,
                turn_id=turn_id,
                changed=changed,
                kind="repo.delete",
            )
        self.retrieval_guard.invalidate()
        return SafeRuntimeResult(True, "repo.delete", data=data, metadata={"changed_paths": changed})

    def _dispatch(self, op, args, turn_id, origin, security_context, capabilities, grant, expected_approval_id):
        if op == "repo.list":
            return self._op_repo_list(args, security_context)
        if op == "repo.write_file":
            result = self._op_registry_tool(
                "repo.write_file",
                "write_file",
                args,
                turn_id,
                origin,
                security_context,
                grant,
                expected_approval_id,
            )
            if result.success:
                self.retrieval_guard.invalidate()
            return result
        if op in {"repo.move", "repo.rename"}:
            return self._op_repo_move(op, args, turn_id, security_context)
        if op == "repo.delete":
            return self._op_repo_delete(args, turn_id, security_context)
        if op in {"repo.definition", "repo.hover", "repo.references_semantic", "repo.diagnostics", "repo.call_hierarchy", "repo.outline", "repo.ast_search", "security.scan"}:
            return self._semantic_dispatch(op, args, security_context)
        return super()._dispatch(op, args, turn_id, origin, security_context, capabilities, grant, expected_approval_id)


def __getattr__(name: str):
    try:
        return getattr(_core, name)
    except AttributeError as exc:
        raise AttributeError(name) from exc


__all__ = ["SafeRuntime", "SafeRuntimeResult", "RuntimeOperationSpec", "OPERATION_SPECS"]
