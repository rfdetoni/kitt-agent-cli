"""Optional Tree-sitter language-pack adapter for repository symbol indexing."""
from __future__ import annotations

from kitt.domain.entities import FileTags, Tag
from kitt.validation.generated_content import tree_sitter_process


def _kind(value) -> str:
    raw = getattr(value, "type", None)
    return str(raw or value or "symbol").replace("_", " ").casefold()


def _walk(items, tags: list[Tag]) -> None:
    for item in list(items or []):
        name = getattr(item, "name", None)
        span = getattr(item, "span", None)
        if name and span is not None:
            start = max(1, int(getattr(span, "start_line", 1) or 1))
            end = max(start, int(getattr(span, "end_line", start) or start))
            kind = _kind(getattr(item, "kind", None))
            signature = str(getattr(item, "signature", None) or name)
            tags.append(
                Tag(
                    kind="def",
                    name=str(name),
                    line=start,
                    end_line=end,
                    signature=signature,
                    sub_kind=kind,
                )
            )
        _walk(getattr(item, "children", None), tags)


def parse_tree_sitter(relative_path: str, content: str) -> FileTags | None:
    result = tree_sitter_process(relative_path, content)
    if result is None:
        return None
    tags: list[Tag] = []
    _walk(getattr(result, "structure", None), tags)
    if not tags:
        for symbol in list(getattr(result, "symbols", None) or []):
            name = getattr(symbol, "name", None)
            span = getattr(symbol, "span", None)
            if not name or span is None:
                continue
            start = max(1, int(getattr(span, "start_line", 1) or 1))
            end = max(start, int(getattr(span, "end_line", start) or start))
            tags.append(
                Tag(
                    kind="def",
                    name=str(name),
                    line=start,
                    end_line=end,
                    signature=str(name),
                    sub_kind=_kind(getattr(symbol, "kind", None)),
                )
            )
    return FileTags(path=relative_path, tags=tags) if tags else None
