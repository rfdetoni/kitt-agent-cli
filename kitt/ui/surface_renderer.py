from __future__ import annotations

from typing import Any


def surface_fragments(surface: dict[str, Any]) -> list[tuple[str, str]]:
    """Project a validated Surface snapshot into prompt_toolkit-friendly text."""
    components = {
        str(item["id"]): item
        for item in surface.get("components", [])
    }
    root = str(surface.get("root") or "")
    output: list[tuple[str, str]] = []
    visited: set[str] = set()

    def walk(node_id: str, depth: int = 0) -> None:
        if node_id in visited or node_id not in components:
            return
        visited.add(node_id)
        node = components[node_id]
        kind = str(node.get("component") or "Text")
        props = node.get("props") or {}
        prefix = "  " * depth
        if kind == "Button":
            text = f"{prefix}[ {props.get('label', '')} ]"
            style = "class:accent"
        elif kind == "Alert":
            text = f"{prefix}! {props.get('text', '')}"
            style = "class:warning"
        elif kind == "Heading":
            text = f"{prefix}{props.get('text', '')}"
            style = "class:primary"
        elif kind == "Metric":
            text = f"{prefix}{props.get('label', '')}: {props.get('value', '')}"
            style = "class:status"
        elif kind == "Progress":
            text = f"{prefix}[{props.get('value', 0)}/{props.get('max', 100)}]"
            style = "class:status"
        else:
            text = (
                prefix
                + str(
                    props.get(
                        "text",
                        props.get("content", props.get("label", kind)),
                    )
                )
            )
            style = "class:text"
        output.append((style, text + "\n"))
        for child in node.get("children") or []:
            walk(str(child), depth + 1)

    walk(root)
    return output
