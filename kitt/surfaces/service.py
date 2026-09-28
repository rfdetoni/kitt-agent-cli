from __future__ import annotations

import json
from typing import Any, Callable

from kitt.surfaces.catalog import ComponentCatalog
from kitt.surfaces.models import SurfaceCapabilities
from kitt.surfaces.store import SurfaceStore


class SurfaceService:
    """Host-owned declarative Surface runtime with no executable UI payloads."""

    def __init__(
        self,
        *,
        event_callback: Callable[[str, dict[str, Any]], None] | None = None,
        max_nodes: int = 256,
        max_depth: int = 16,
    ):
        self.catalog = ComponentCatalog()
        self.store = SurfaceStore(
            self.catalog,
            max_nodes=max_nodes,
            max_depth=max_depth,
        )
        self.event_callback = event_callback

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        if self.event_callback:
            try:
                self.event_callback(event, payload)
            except Exception:
                pass

    def capabilities(self) -> dict[str, Any]:
        return SurfaceCapabilities(
            components=self.catalog.names,
            max_depth=self.store.max_depth,
            max_nodes=self.store.max_nodes,
        ).__dict__

    def publish(self, spec: dict[str, Any]) -> dict[str, Any]:
        snapshot = self.store.upsert(spec)
        self._emit(
            "SurfacePublished",
            {
                "surface_id": snapshot.id,
                "revision": snapshot.revision,
                "catalog_id": snapshot.catalog_id,
            },
        )
        return snapshot.to_dict()

    def patch(
        self,
        surface_id: str,
        base_revision: int,
        operations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        snapshot = self.store.patch(surface_id, base_revision, operations)
        self._emit(
            "SurfacePatched",
            {
                "surface_id": snapshot.id,
                "revision": snapshot.revision,
                "operations": len(operations),
            },
        )
        return snapshot.to_dict()

    def get(self, surface_id: str) -> dict[str, Any] | None:
        snapshot = self.store.get(surface_id)
        return snapshot.to_dict() if snapshot else None

    def delete(self, surface_id: str) -> bool:
        deleted = self.store.delete(surface_id)
        if deleted:
            self._emit("SurfaceDeleted", {"surface_id": surface_id})
        return deleted

    def action(
        self,
        surface_id: str,
        component_id: str,
        action: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        snapshot = self.store.get(surface_id)
        if snapshot is None:
            raise KeyError(surface_id)
        component = next(
            (item for item in snapshot.components if item.id == component_id),
            None,
        )
        if component is None:
            raise KeyError(component_id)
        if component.props.get("action") != action:
            raise PermissionError("surface action is not registered on the component")
        payload = {
            "surface_id": surface_id,
            "component_id": component_id,
            "action": action,
            "context": dict(context or {}),
            "revision": snapshot.revision,
        }
        self._emit("SurfaceAction", payload)
        return payload

    def render_text(self, surface_id: str) -> str:
        snapshot = self.store.get(surface_id)
        if snapshot is None:
            raise KeyError(surface_id)
        by_id = {item.id: item for item in snapshot.components}
        lines: list[str] = []

        def render(node_id: str, depth: int = 0) -> None:
            node = by_id[node_id]
            props = node.props
            prefix = "  " * depth
            if node.component in {"Text", "Markdown"}:
                lines.append(prefix + str(props.get("text", props.get("content", ""))))
            elif node.component == "Heading":
                lines.append(prefix + str(props.get("text", "")))
            elif node.component == "Metric":
                lines.append(
                    prefix + f"{props.get('label', '')}: {props.get('value', '')}"
                )
            elif node.component == "Progress":
                lines.append(
                    prefix + f"[{props.get('value', 0)}/{props.get('max', 100)}]"
                )
            elif node.component == "Button":
                lines.append(prefix + f"[ {props.get('label', '')} ]")
            elif node.component == "Alert":
                lines.append(prefix + f"! {props.get('text', '')}")
            elif node.component == "Artifact":
                lines.append(prefix + f"artifact:{props.get('artifact_id', '')}")
            else:
                lines.append(
                    prefix
                    + str(props.get("label") or props.get("title") or node.component)
                )
            for child in node.children:
                render(child, depth + 1)

        render(snapshot.root)
        return "\n".join(lines)

    def parse_stream_line(self, line: str) -> dict[str, Any] | None:
        stripped = line.strip()
        if not stripped:
            return None
        payload = json.loads(stripped)
        if not isinstance(payload, dict):
            raise ValueError("surface stream record must be a JSON object")
        op = str(payload.get("op") or "")
        if op == "publish":
            return self.publish(dict(payload.get("surface") or {}))
        if op == "patch":
            return self.patch(
                str(payload.get("surface_id") or ""),
                int(payload.get("base_revision", -1)),
                list(payload.get("operations") or []),
            )
        if op == "delete":
            return {"deleted": self.delete(str(payload.get("surface_id") or ""))}
        raise ValueError(f"unsupported surface stream op: {op}")
