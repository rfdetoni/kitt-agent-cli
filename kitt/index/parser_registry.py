"""Minimal parser registry for repository indexing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from kitt.context_engine.parser import SymbolParser
from kitt.domain.entities import FileTags


@dataclass(frozen=True)
class ParserAdapter:
    id: str
    version: str
    extensions: frozenset[str]


class ParserRegistry:
    """Select parser adapters by file extension without making optional parsers mandatory."""

    version = "parser-registry-v1"

    def __init__(self, symbol_parser: SymbolParser | None = None):
        self.symbol_parser = symbol_parser or SymbolParser()
        self.adapters = (
            ParserAdapter("stdlib-symbol-parser", "v1", frozenset({
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
        return self.symbol_parser.extract_file_tags(file_path, relative_path, content=content)
