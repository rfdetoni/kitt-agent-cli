"""Minimal parser registry for repository indexing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from kitt.context_engine.parser import SymbolParser
from kitt.domain.entities import FileTags
from kitt.formatting.contract import language_for_path
from kitt.index.tree_sitter_parser import parse_tree_sitter


@dataclass(frozen=True)
class ParserAdapter:
    id: str
    version: str
    extensions: frozenset[str]


class ParserRegistry:
    """Select parser adapters by file extension without making optional parsers mandatory."""

    version = "parser-registry-v2"

    def __init__(self, symbol_parser: SymbolParser | None = None):
        self.symbol_parser = symbol_parser or SymbolParser()
        self.adapters = (
            ParserAdapter("tree-sitter-language-pack", "v1", frozenset({
                ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".cs",
                ".dart", ".go", ".java", ".js", ".jsx", ".mjs", ".cjs",
                ".kt", ".kts", ".php", ".py", ".pyi", ".rb", ".rs", ".scala",
                ".swift", ".ts", ".tsx", ".zig",
            })),
            ParserAdapter("stdlib-symbol-parser", "v2", frozenset({
                ".py", ".java", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go", ".rs",
            })),
            ParserAdapter("generic-symbol-parser", "v1", frozenset()),
        )

    def adapter_for(self, path: Path) -> ParserAdapter:
        suffix = path.suffix.lower()
        for adapter in self.adapters:
            if suffix in adapter.extensions:
                return adapter
        return self.adapters[-1]

    def parse(self, file_path: Path, relative_path: str, content: str | None = None) -> Optional[FileTags]:
        text = content
        if text is None:
            try:
                text = file_path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                text = None
        if text is not None and language_for_path(relative_path):
            parsed = parse_tree_sitter(relative_path, text)
            if parsed is not None:
                return parsed
        return self.symbol_parser.extract_file_tags(file_path, relative_path, content=text)
