"""Deterministic formatting and readability gates for generated workspace files."""
from __future__ import annotations

import ast
import json
import os
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
    language: str | None = None


BRACE_SOURCE_SUFFIXES = frozenset({
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp",
    ".cs", ".css", ".go", ".java", ".js", ".jsx", ".kt", ".kts",
    ".less", ".mjs", ".php", ".rs", ".scss", ".swift", ".ts", ".tsx",
})
_MARKUP_SUFFIXES = frozenset({".html", ".htm", ".xhtml", ".vue", ".svelte"})
_XML_SUFFIXES = frozenset({".xml", ".xhtml", ".svg"})
_MINIFIED_NAME_RE = re.compile(r"\.min\.(?:css|js|mjs|json)$", re.IGNORECASE)

SOURCE_SUFFIXES = BRACE_SOURCE_SUFFIXES | frozenset({
    ".py", ".pyi", ".rb", ".scala", ".lua", ".r", ".ex", ".exs",
    ".erl", ".fs", ".fsx", ".sh", ".bash", ".zsh", ".sql",
    ".html", ".htm", ".xhtml", ".vue", ".svelte", ".yaml", ".yml",
})
_FALLBACK_LANGUAGE = {
    ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp",
    ".hh": "cpp", ".hpp": "cpp", ".cs": "c_sharp", ".dart": "dart",
    ".go": "go", ".java": "java", ".js": "javascript", ".jsx": "javascript",
    ".mjs": "javascript", ".cjs": "javascript", ".kt": "kotlin",
    ".kts": "kotlin", ".php": "php", ".py": "python", ".pyi": "python",
    ".rb": "ruby", ".rs": "rust", ".scala": "scala", ".swift": "swift",
    ".ts": "typescript", ".tsx": "tsx", ".zig": "zig", ".css": "css",
    ".scss": "scss", ".less": "less", ".html": "html", ".htm": "html",
    ".xhtml": "html", ".vue": "vue", ".svelte": "svelte", ".yaml": "yaml",
    ".yml": "yaml", ".toml": "toml", ".json": "json", ".xml": "xml",
    ".sh": "bash", ".bash": "bash", ".zsh": "bash", ".sql": "sql",
    ".lua": "lua", ".r": "r", ".ex": "elixir", ".exs": "elixir",
}


@dataclass
class _LexState:
    block_comment: bool = False
    quote: str | None = None
    escaped: bool = False
    text_block: bool = False


def detect_source_language(path: str) -> str | None:
    """Detect a language without requiring parser downloads."""
    try:
        from tree_sitter_language_pack import detect_language_from_path
        detected = detect_language_from_path(path)
        if detected:
            return str(detected)
    except Exception:
        pass
    return _FALLBACK_LANGUAGE.get(Path(path).suffix.casefold())


def _tree_sitter_enabled(path: str) -> bool:
    language = detect_source_language(path)
    if not language:
        return False
    allow_download = os.getenv("KITT_TREE_SITTER_AUTO_DOWNLOAD", "").strip().casefold() in {
        "1", "true", "yes", "on", "enabled",
    }
    try:
        from tree_sitter_language_pack import downloaded_languages, has_language
        if not has_language(language):
            return False
        cached = {str(item).casefold() for item in downloaded_languages()}
        return language.casefold() in cached or allow_download
    except Exception:
        return False


def tree_sitter_process(path: str, content: str):
    """Parse structurally when tree-sitter-language-pack is available and cached."""
    language = detect_source_language(path)
    if not language or not _tree_sitter_enabled(path):
        return None
    try:
        from tree_sitter_language_pack import ProcessConfig, process
        return process(
            content,
            ProcessConfig(
                language=language,
                structure=True,
                imports=True,
                exports=False,
                comments=False,
                docstrings=False,
                symbols=True,
                diagnostics=True,
                parse_timeout_ms=2_000,
                max_source_bytes=max(
                    1_048_576,
                    min(len(content.encode("utf-8")) + 1024, 8 * 1024 * 1024),
                ),
            ),
        )
    except Exception:
        return None


def _indent_unit(
    existing_content: str | None,
    content: str,
    suffix: str,
    style: dict | None = None,
) -> str:
    style = style if isinstance(style, dict) else {}
    preferred_style = str(style.get("indent_style", "")).casefold()
    preferred_size = style.get("indent_size")
    default = "\t" if suffix == ".go" else "    "
    if existing_content is None:
        if preferred_style in {"tab", "tabs"}:
            default = "\t"
        elif preferred_style in {"space", "spaces"}:
            try:
                width = max(1, min(int(preferred_size), 16))
            except (TypeError, ValueError):
                width = 4
            default = " " * width
    samples: list[str] = []
    for candidate in (existing_content or "", content):
        for line in candidate.splitlines():
            if not line.strip():
                continue
            prefix = line[: len(line) - len(line.lstrip(" \t"))]
            if prefix:
                samples.append(prefix)
        if existing_content and samples:
            break
    if not samples:
        return default
    if sum(1 for prefix in samples if prefix and set(prefix) == {"\t"}) > len(samples) // 2:
        return "\t"
    widths = [len(prefix) for prefix in samples if len(prefix) >= 2 and set(prefix) == {" "}]
    if widths:
        return " " * min(max(min(widths), 2), 8)
    return default


def _scan_line(line: str, state: _LexState) -> tuple[int, int, int, int, int, int, int, int, int]:
    bo = bc = po = pc = so = sc = 0
    lead_b = lead_p = lead_s = 0
    seen = False
    index = 0
    while index < len(line):
        ch = line[index]
        nxt = line[index + 1] if index + 1 < len(line) else ""
        if state.text_block:
            marker_index = line.find('"""', index)
            if marker_index < 0:
                return bo, bc, po, pc, so, sc, lead_b, lead_p, lead_s
            state.text_block = False
            index = marker_index + 3
            continue
        if state.block_comment:
            if ch == "*" and nxt == "/":
                state.block_comment = False
                index += 2
            else:
                index += 1
            continue
        if state.quote:
            if state.escaped:
                state.escaped = False
            elif ch == "\\":
                state.escaped = True
            elif ch == state.quote:
                state.quote = None
            index += 1
            continue
        if line.startswith('"""', index):
            state.text_block = True
            seen = True
            index += 3
            continue
        if ch == "/" and nxt == "/":
            break
        if ch == "/" and nxt == "*":
            state.block_comment = True
            index += 2
            continue
        if ch in {'"', "'", "`"}:
            state.quote = ch
            state.escaped = False
            seen = True
            index += 1
            continue
        if ch.isspace():
            index += 1
            continue
        if not seen:
            lead_b = int(ch == "}")
            lead_p = int(ch == ")")
            lead_s = int(ch == "]")
            seen = True
        bo += int(ch == "{")
        bc += int(ch == "}")
        po += int(ch == "(")
        pc += int(ch == ")")
        so += int(ch == "[")
        sc += int(ch == "]")
        index += 1
    if state.quote in {'"', "'"}:
        state.quote = None
        state.escaped = False
    return bo, bc, po, pc, so, sc, lead_b, lead_p, lead_s


def _heal_brace_indentation(
    path: str,
    content: str,
    existing_content: str | None,
    style: dict | None = None,
) -> str:
    suffix = Path(path).suffix.casefold()
    unit = _indent_unit(existing_content, content, suffix, style)
    reference = existing_content if existing_content is not None else content
    newline = "\r\n" if "\r\n" in reference else "\r" if "\r" in reference and "\n" not in reference else "\n"
    final_newline = content.endswith(("\n", "\r"))
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if final_newline and lines and lines[-1] == "":
        lines.pop()

    brace = paren = bracket = 0
    state = _LexState()
    healed: list[str] = []
    for raw in lines:
        stripped = raw.lstrip(" \t")
        if not stripped:
            healed.append("")
            continue
        started_literal = bool(state.block_comment or state.quote or state.text_block)
        bo, bc, po, pc, so, sc, lead_b, lead_p, lead_s = _scan_line(stripped, state)
        if started_literal:
            healed.append(raw)
        elif stripped.startswith("#") and suffix in {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp"}:
            healed.append(stripped)
        else:
            depth = max(0, brace - lead_b)
            continuation = int((paren - lead_p) > 0 or (bracket - lead_s) > 0)
            healed.append(unit * (depth + continuation) + stripped)
        brace = max(0, brace + bo - bc)
        paren = max(0, paren + po - pc)
        bracket = max(0, bracket + so - sc)
    return newline.join(healed) + (newline if final_newline else "")


def structural_issues(path: str, content: str) -> list[str]:
    """Return deterministic syntax-shape errors for brace-delimited languages."""
    if Path(path).suffix.casefold() not in BRACE_SOURCE_SUFFIXES:
        return []
    brace = paren = bracket = 0
    state = _LexState()
    issues: list[str] = []
    for lineno, line in enumerate(content.splitlines(), start=1):
        bo, bc, po, pc, so, sc, *_ = _scan_line(line, state)
        brace += bo - bc
        paren += po - pc
        bracket += so - sc
        if brace < 0:
            issues.append(f"line {lineno}: unexpected closing brace")
            brace = 0
        if paren < 0:
            issues.append(f"line {lineno}: unexpected closing parenthesis")
            paren = 0
        if bracket < 0:
            issues.append(f"line {lineno}: unexpected closing bracket")
            bracket = 0
    if state.block_comment:
        issues.append("unterminated block comment")
    if state.quote == "`":
        issues.append("unterminated template literal")
    if state.text_block:
        issues.append("unterminated text block")
    if brace:
        issues.append(f"unbalanced braces: depth={brace}")
    if paren:
        issues.append(f"unbalanced parentheses: depth={paren}")
    if bracket:
        issues.append(f"unbalanced brackets: depth={bracket}")
    return issues[:8]


def tree_sitter_issues(path: str, content: str) -> list[str] | None:
    result = tree_sitter_process(path, content)
    if result is None:
        return None
    try:
        if int(result.metrics.error_count or 0) <= 0:
            return []
    except Exception:
        pass
    messages: list[str] = []
    for diagnostic in list(getattr(result, "diagnostics", None) or [])[:16]:
        severity = str(getattr(diagnostic, "severity", "")).casefold()
        if severity and "error" not in severity:
            continue
        span = getattr(diagnostic, "span", None)
        line = int(getattr(span, "start_line", 1) or 1)
        message = str(getattr(diagnostic, "message", None) or "parse error")
        messages.append(f"line {line}: {message}")
    return messages[:8] or ["tree-sitter reported syntax error nodes"]


def heal_source_content(
    path: str,
    content: str,
    *,
    existing_content: str | None = None,
    style: dict | None = None,
) -> PreparedGeneratedContent:
    suffix = Path(path).suffix.casefold()
    language = detect_source_language(path)
    if suffix == ".json":
        normalized = _pretty_json(content, existing_content, style)
        return PreparedGeneratedContent(normalized, normalized != content, "json.pretty", language)
    if suffix in BRACE_SOURCE_SUFFIXES:
        healed = _heal_brace_indentation(path, content, existing_content, style)
        return PreparedGeneratedContent(
            healed,
            healed != content,
            "auto-heal.brace-indent" if healed != content else "preserve",
            language,
        )
    return PreparedGeneratedContent(content, False, "preserve", language)



def _json_indent(existing_content: str | None, style: dict | None = None) -> int | str:
    if not existing_content:
        style = style if isinstance(style, dict) else {}
        if str(style.get("indent_style", "")).casefold() in {"tab", "tabs"}:
            return "\t"
        try:
            return max(1, min(int(style.get("indent_size", 2)), 16))
        except (TypeError, ValueError):
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


def _pretty_json(
    content: str,
    existing_content: str | None,
    style: dict | None = None,
) -> str:
    try:
        value = json.loads(content)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise GeneratedContentError(f"invalid JSON: {exc}") from exc
    rendered = json.dumps(
        value,
        ensure_ascii=False,
        indent=_json_indent(existing_content, style),
        sort_keys=False,
    )
    return rendered + "\n"


def _validate_syntax(path: str, content: str) -> None:
    suffix = Path(path).suffix.casefold()
    try:
        if suffix in {".py", ".pyi"}:
            ast.parse(content, filename=path)
        elif suffix == ".toml":
            tomllib.loads(content)
        elif suffix in _XML_SUFFIXES:
            DefusedET.fromstring(content)
    except (SyntaxError, ValueError, UnicodeError, ParseError, DefusedXmlException) as exc:
        raise GeneratedContentError(
            f"{path} is syntactically invalid; preserve required indentation/structure: {exc}"
        ) from exc

    issues = structural_issues(path, content)
    if issues:
        raise GeneratedContentError(f"{path} has invalid structure: {'; '.join(issues)}")
    parsed_issues = tree_sitter_issues(path, content)
    if parsed_issues:
        raise GeneratedContentError(
            f"{path} failed Tree-sitter validation: {'; '.join(parsed_issues)}"
        )

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

    if suffix in BRACE_SOURCE_SUFFIXES:
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


def validate_generated_content(path: str, content: str) -> None:
    """Validate final content without rewriting it."""
    if "\x00" in content:
        raise GeneratedContentError(f"{path} contains NUL bytes")
    _validate_syntax(path, content)
    _assert_readable_layout(path, content)


def prepare_generated_content(
    path: str,
    content: str,
    *,
    existing_content: str | None = None,
    style: dict | None = None,
) -> PreparedGeneratedContent:
    """Heal safe formatting defects, then fail closed on malformed source."""
    if "\x00" in content:
        raise GeneratedContentError(f"{path} contains NUL bytes")
    prepared = heal_source_content(
        path,
        content,
        existing_content=existing_content,
        style=style,
    )
    validate_generated_content(path, prepared.content)
    return prepared
