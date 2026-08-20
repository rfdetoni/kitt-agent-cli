from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import fallback


@dataclass(frozen=True)
class NativeEngineStatus:
    backend: str
    version: str
    available: bool
    detail: str = ""


class NativeCodeEngine:
    """Stable Python-facing KITT code-intelligence API with native-first fallback."""

    def __init__(self, root_dir: str):
        self.root = Path(root_dir).resolve()
        self._native: Any = None
        self._native_module: Any = None
        try:
            import kitt_native  # type: ignore
            self._native_module = kitt_native
            self._native = kitt_native.Engine(str(self.root))
            self.status = NativeEngineStatus("rust", str(getattr(kitt_native, "ENGINE_VERSION", "unknown")), True)
        except Exception as exc:
            self.status = NativeEngineStatus("python", "fallback-v1", False, str(exc))

    @staticmethod
    def _loads(value: Any) -> Any:
        return json.loads(value) if isinstance(value, str) else value

    def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
        if self._native is not None:
            return self._loads(self._native.search(
                query, bool(kwargs.get("regex", False)), bool(kwargs.get("case_sensitive", False)),
                int(kwargs.get("max_results", 50)), int(kwargs.get("max_per_file", 8)),
                int(kwargs.get("context_lines", 1)), int(kwargs.get("token_budget", 1200)),
            ))
        return fallback.search(self.root, query, **kwargs)

    def find_symbols(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        if self._native is not None:
            return self._loads(self._native.find_symbols(query, limit))
        return fallback.find_symbols(self.root, query, limit)

    def read_symbol(self, symbol_id: str) -> dict[str, Any] | None:
        if self._native is not None:
            return self._loads(self._native.read_symbol(symbol_id))
        return fallback.read_symbol(self.root, symbol_id)

    def references(self, symbol_id: str, limit: int = 100) -> list[dict[str, Any]]:
        if self._native is not None:
            return self._loads(self._native.references(symbol_id, limit))
        return fallback.references(self.root, symbol_id, limit)

    def dependency_edges(self, max_symbols: int = 10000) -> dict[str, list[str]]:
        if self._native is not None:
            return self._loads(self._native.dependency_edges(max_symbols))
        return fallback.dependency_edges(self.root, max_symbols)

    def replace_symbol(self, symbol_id: str, replacement: str, expected_hash: str | None = None,
                       validate_syntax: bool = True) -> dict[str, Any]:
        if self._native is not None:
            return self._loads(self._native.replace_symbol(symbol_id, replacement, expected_hash, validate_syntax))
        return fallback.replace_symbol(self.root, symbol_id, replacement, expected_hash, validate_syntax)

    def compress_output(self, argv: list[str], stdout: str, stderr: str, returncode: int) -> dict[str, Any]:
        if self._native_module is not None:
            return self._loads(self._native_module.compress_output(argv, stdout, stderr, returncode))
        return fallback.compress_output(argv, stdout, stderr, returncode)
