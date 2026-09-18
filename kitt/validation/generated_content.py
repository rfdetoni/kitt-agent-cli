"""Deterministic formatting and readability gates for generated workspace files."""
from __future__ import annotations

import ast
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from xml.etree.ElementTree import ParseError

from defusedxml import ElementTree as DefusedET
from defusedxml.common import DefusedXmlException


class GeneratedContentError(ValueError):
    """Raised when generated file content is unsafe or structurally unreadable."""


@dataclass(frozen=True)
class PreparedGeneratedContent:
    content: str
    normalized: bool = False
    strategy: str = "preserve"


_BRACE_SOURCE_SUFFIXES = frozenset({
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp",
    ".cs", ".css", ".go", ".java", ".js", ".jsx", ".kt", ".kts",
    ".less", ".mjs", ".php", ".rs", ".scss", ".swift", ".ts", ".tsx",
})
_MARKUP_SUFFIXES = frozenset({".html", ".htm", ".xhtml", ".vue", ".svelte"})
_XML_SUFFIXES = frozenset({".xml", ".xhtml", ".svg"})
_MINIFIED_NAME_RE = re.compile(r"\.min\.(?:css|js|mjs|json)$", re.IGNORECASE)


def _json_indent(existing_content: str | None) -> int | str:
    if not existing_content:
        return 2
    for line in existing_content.splitlines()[1:]:
        if not line.strip():
            continue
        prefix = line[: len(line) - len(line.lstrip(" \t"))]
        if not prefix:
            continue
        if "\t" in prefix and prefix.strip("\t") == "":
            return "\t"
        if prefix.strip(" ") == "":
            return max(1, min(len(prefix), 8))
    return 2


def _pretty_json(content: str, existing_content: str | None) -> str:
    try:
        value = json.loads(content)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise GeneratedContentError(f"invalid JSON: {exc}") from exc
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        indent=_json_indent(existing_content),
        sort_keys=False,
    )
    return rendered + "\n"


def _validate_syntax(path: str, content: str) -> None:
    suffix = Path(path).suffix.casefold()
    try:
        if suffix == ".py":
            ast.parse(content, filename=path)
        elif suffix == ".toml":
            tomllib.loads(content)
        elif suffix in _XML_SUFFIXES:
            DefusedET.fromstring(content)
    except (SyntaxError, ValueError, UnicodeError, ParseError, DefusedXmlException) as exc:
        raise GeneratedContentError(
            f"{path} is syntactically invalid; preserve required indentation/structure: {exc}"
        ) from exc


def _leading_whitespace(line: str) -> bool:
    return bool(line) and line[0] in {" ", "\t"}


def _assert_readable_layout(path: str, content: str) -> None:
    candidate = Path(path)
    name = candidate.name
    suffix = candidate.suffix.casefold()
    if _MINIFIED_NAME_RE.search(name):
        return

    lines = [line for line in content.splitlines() if line.strip()]
    if not lines:
        return

    if suffix in _BRACE_SOURCE_SUFFIXES:
        structural_tokens = content.count("{") + content.count("}")
        if (
            len(lines) == 1
            and len(lines[0]) >= 320
            and structural_tokens >= 4
        ):
            raise GeneratedContentError(
                f"{path} looks minified/flattened; generated source must preserve normal line breaks and indentation"
            )
        if (
            len(lines) >= 6
            and structural_tokens >= 4
            and not any(_leading_whitespace(line) for line in lines)
        ):
            raise GeneratedContentError(
                f"{path} is fully left-aligned despite nested blocks; generated source must preserve normal indentation"
            )

    if suffix in _MARKUP_SUFFIXES:
        tag_count = len(re.findall(r"</?[A-Za-z][^>]*>", content))
        if len(lines) == 1 and len(lines[0]) >= 320 and tag_count >= 6:
            raise GeneratedContentError(
                f"{path} looks minified/flattened; generated markup must preserve readable line breaks and indentation"
            )


def prepare_generated_content(
    path: str,
    content: str,
    *,
    existing_content: str | None = None,
) -> PreparedGeneratedContent:
    """Normalize losslessly safe formats and reject malformed/flattened generated source."""
    if "\x00" in content:
        raise GeneratedContentError(f"{path} contains NUL bytes")

    suffix = Path(path).suffix.casefold()
    normalized = content
    strategy = "preserve"

    if suffix == ".json":
        normalized = _pretty_json(content, existing_content)
        strategy = "json.pretty"

    _validate_syntax(path, normalized)
    _assert_readable_layout(path, normalized)

    return PreparedGeneratedContent(
        content=normalized,
        normalized=normalized != content,
        strategy=strategy,
    )
