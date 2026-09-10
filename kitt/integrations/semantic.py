from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from kitt.integrations.ast_grep import AstGrepAdapter
from kitt.integrations.lsp import LanguageServerClient
from kitt.integrations.semgrep import SemgrepAdapter
from kitt.native.bridge import NativeCodeEngine
from kitt.security.workspace_fs import WorkspaceFileSystem

_IDENTIFIER_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")


class SemanticCodeIntelligence:
    """Native-first semantic facade with optional LSP/ast-grep/Semgrep adapters."""

    def __init__(self, root_dir: str | Path):
        self.root = Path(root_dir).resolve()
        self.fs = WorkspaceFileSystem(self.root)
        self.native = NativeCodeEngine(str(self.root))
        self.lsp = LanguageServerClient(self.root)
        self.ast_grep = AstGrepAdapter(self.root)
        self.semgrep = SemgrepAdapter(self.root)

    def _safe_relative(self, raw: str) -> str:
        return self.fs.relative(raw)

    def _identifier_at(self, path: str, line: int, column: int) -> str:
        relative = self._safe_relative(path)
        data = self.fs.read(relative, max_bytes=4 * 1024 * 1024)
        lines = data.content.decode("utf-8", errors="replace").splitlines()
        if not lines:
            return ""
        index = max(0, min(int(line) - 1, len(lines) - 1))
        text = lines[index]
        cursor = max(0, min(int(column), len(text)))
        for match in _IDENTIFIER_RE.finditer(text):
            if match.start() <= cursor <= match.end():
                return match.group(0)
        return ""

    def _lsp_request(self, path: str, method: str, **kwargs: Any) -> dict[str, Any]:
        try:
            return self.lsp.request(path, method, **kwargs)
        except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
            return {"backend": "lsp", "available": False, "method": method, "result": None, "detail": str(exc)[:1000]}

    def definition(self, path: str, line: int, column: int = 0) -> dict[str, Any]:
        relative = self._safe_relative(path)
        lsp = self._lsp_request(relative, "textDocument/definition", line=line, column=column)
        if lsp.get("available") and lsp.get("result") is not None:
            return lsp
        identifier = self._identifier_at(relative, line, column)
        symbols = self.native.find_symbols(identifier, limit=20) if identifier else []
        exact = [symbol for symbol in symbols if str(symbol.get("name", "")).casefold() == identifier.casefold()]
        return {"backend": f"native:{self.native.status.backend}", "available": True, "identifier": identifier, "result": exact[:5] or symbols[:5]}

    def hover(self, path: str, line: int, column: int = 0) -> dict[str, Any]:
        relative = self._safe_relative(path)
        lsp = self._lsp_request(relative, "textDocument/hover", line=line, column=column)
        if lsp.get("available") and lsp.get("result") is not None:
            return lsp
        identifier = self._identifier_at(relative, line, column)
        symbols = self.native.find_symbols(identifier, limit=5) if identifier else []
        return {"backend": f"native:{self.native.status.backend}", "available": bool(symbols), "identifier": identifier, "result": symbols[:1]}

    def references(self, path: str, line: int, column: int = 0, *, limit: int = 100) -> dict[str, Any]:
        relative = self._safe_relative(path)
        lsp = self._lsp_request(
            relative,
            "textDocument/references",
            line=line,
            column=column,
            params={
                "textDocument": {"uri": (self.root / relative).as_uri()},
                "position": {"line": max(0, int(line) - 1), "character": max(0, int(column))},
                "context": {"includeDeclaration": True},
            },
        )
        if lsp.get("available") and lsp.get("result") is not None:
            result = lsp.get("result")
            if isinstance(result, list):
                lsp["result"] = result[: max(1, min(int(limit), 1000))]
            return lsp
        identifier = self._identifier_at(relative, line, column)
        symbols = self.native.find_symbols(identifier, limit=5) if identifier else []
        if not symbols:
            return {"backend": f"native:{self.native.status.backend}", "available": False, "result": []}
        symbol_id = str(symbols[0].get("id") or symbols[0].get("symbol_id") or "")
        refs = self.native.references(symbol_id, limit=max(1, min(int(limit), 1000))) if symbol_id else []
        return {"backend": f"native:{self.native.status.backend}", "available": bool(symbol_id), "identifier": identifier, "result": refs}

    def outline(self, path: str) -> dict[str, Any]:
        relative = self._safe_relative(path)
        lsp = self._lsp_request(relative, "textDocument/documentSymbol", params={"textDocument": {"uri": (self.root / relative).as_uri()}})
        if lsp.get("available") and lsp.get("result") is not None:
            return lsp
        return {"backend": f"native:{self.native.status.backend}", "available": False, "result": [], "detail": "No language server available for document outline"}

    def diagnostics(self, path: str) -> dict[str, Any]:
        relative = self._safe_relative(path)
        result = self._lsp_request(relative, "textDocument/diagnostic", params={"textDocument": {"uri": (self.root / relative).as_uri()}})
        if result.get("available"):
            return result
        return {"backend": "lsp", "available": False, "result": [], "detail": result.get("detail", "Pull diagnostics unavailable for this language server")}

    def call_hierarchy(self, path: str, line: int, column: int = 0) -> dict[str, Any]:
        relative = self._safe_relative(path)
        return self._lsp_request(relative, "textDocument/prepareCallHierarchy", line=line, column=column)

    def ast_search(self, pattern: str, **kwargs: Any) -> dict[str, Any]:
        return self.ast_grep.search(pattern, **kwargs)

    def security_scan(self, paths: list[str], **kwargs: Any) -> dict[str, Any]:
        safe_paths = [self._safe_relative(path) for path in paths]
        return self.semgrep.scan_changed(paths=safe_paths, **kwargs)
