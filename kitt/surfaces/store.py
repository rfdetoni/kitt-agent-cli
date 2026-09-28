from __future__ import annotations

import copy
import threading
from typing import Any

from kitt.surfaces.catalog import ComponentCatalog
from kitt.surfaces.models import ComponentNode, SurfaceSnapshot


class SurfaceValidationError(ValueError):
    pass


class StaleSurfaceError(SurfaceValidationError):
    pass


class SurfaceStore:
    def __init__(
        self,
        catalog: ComponentCatalog,
        *,
        max_nodes: int = 256,
        max_depth: int = 16,
    ):
        self.catalog = catalog
        self.max_nodes = max(1, min(int(max_nodes), 2048))
        self.max_depth = max(1, min(int(max_depth), 64))
        self._items: dict[str, SurfaceSnapshot] = {}
        self._lock = threading.RLock()

    def _validate_graph(self, spec: dict[str, Any]) -> SurfaceSnapshot:
        surface_id = str(spec.get("id") or "").strip()
        catalog_id = str(spec.get("catalog_id") or self.catalog.catalog_id).strip()
        root = str(spec.get("root") or "").strip()
        revision = int(spec.get("revision", 0) or 0)
        raw_components = spec.get("components") or []
        state = spec.get("state") or {}
        metadata = spec.get("metadata") or {}

        if not surface_id or len(surface_id) > 128:
            raise SurfaceValidationError("surface id must be 1..128 characters")
        if catalog_id != self.catalog.catalog_id:
            raise SurfaceValidationError(f"unsupported catalog: {catalog_id}")
        if not isinstance(raw_components, list) or len(raw_components) > self.max_nodes:
            raise SurfaceValidationError(f"surface exceeds max_nodes={self.max_nodes}")
        if not isinstance(state, dict) or not isinstance(metadata, dict):
            raise SurfaceValidationError("surface state and metadata must be objects")

        components: list[ComponentNode] = []
        seen: set[str] = set()
        for raw in raw_components:
            if not isinstance(raw, dict):
                raise SurfaceValidationError("components must be objects")
            errors = self.catalog.validate(raw)
            if errors:
                raise SurfaceValidationError("; ".join(errors))
            node_id = str(raw["id"]).strip()
            if node_id in seen:
                raise SurfaceValidationError(f"duplicate component id: {node_id}")
            seen.add(node_id)
            components.append(
                ComponentNode(
                    id=node_id,
                    component=str(raw["component"]).strip(),
                    props=copy.deepcopy(raw.get("props") or {}),
                    children=tuple(str(value).strip() for value in raw.get("children") or []),
                )
            )

        if root not in seen:
            raise SurfaceValidationError("surface root does not reference a component")

        by_id = {node.id: node for node in components}
        for node in components:
            missing = [child for child in node.children if child not in by_id]
            if missing:
                raise SurfaceValidationError(
                    f"{node.id}: unknown child reference(s): {', '.join(missing)}"
                )

        visiting: set[str] = set()
        visited: set[str] = set()

        def walk(node_id: str, depth: int) -> None:
            if depth > self.max_depth:
                raise SurfaceValidationError(f"surface exceeds max_depth={self.max_depth}")
            if node_id in visiting:
                raise SurfaceValidationError(f"surface component cycle detected at {node_id}")
            if node_id in visited:
                return
            visiting.add(node_id)
            for child in by_id[node_id].children:
                walk(child, depth + 1)
            visiting.remove(node_id)
            visited.add(node_id)

        walk(root, 1)
        return SurfaceSnapshot(
            id=surface_id,
            revision=max(0, revision),
            catalog_id=catalog_id,
            root=root,
            components=tuple(components),
            state=copy.deepcopy(state),
            metadata=copy.deepcopy(metadata),
        )

    def upsert(self, spec: dict[str, Any]) -> SurfaceSnapshot:
        incoming = self._validate_graph(spec)
        with self._lock:
            current = self._items.get(incoming.id)
            revision = 1 if current is None else current.revision + 1
            snapshot = SurfaceSnapshot(**{**incoming.__dict__, "revision": revision})
            self._items[snapshot.id] = snapshot
            return snapshot

    def get(self, surface_id: str) -> SurfaceSnapshot | None:
        with self._lock:
            return self._items.get(str(surface_id or "").strip())

    def list(self) -> list[SurfaceSnapshot]:
        with self._lock:
            return list(self._items.values())

    def delete(self, surface_id: str) -> bool:
        with self._lock:
            return self._items.pop(str(surface_id or "").strip(), None) is not None

    def patch(
        self,
        surface_id: str,
        base_revision: int,
        operations: list[dict[str, Any]],
    ) -> SurfaceSnapshot:
        with self._lock:
            current = self._items.get(surface_id)
            if current is None:
                raise SurfaceValidationError(f"unknown surface: {surface_id}")
            if int(base_revision) != current.revision:
                raise StaleSurfaceError(
                    f"stale surface patch: base={base_revision}, current={current.revision}"
                )

            spec = current.to_dict()
            components = {item["id"]: item for item in spec["components"]}
            order = list(components)
            for op in operations[:256]:
                if not isinstance(op, dict):
                    raise SurfaceValidationError("patch operations must be objects")
                kind = str(op.get("op") or "").strip()
                target = str(op.get("target") or "").strip()
                value = copy.deepcopy(op.get("value"))

                if kind in {"add", "replace"} and target.startswith("component:"):
                    component_id = target.split(":", 1)[1]
                    if not isinstance(value, dict):
                        raise SurfaceValidationError("component patch value must be an object")
                    value.setdefault("id", component_id)
                    if value["id"] != component_id:
                        raise SurfaceValidationError("component patch id does not match target")
                    if kind == "add" and component_id in components:
                        raise SurfaceValidationError(f"component already exists: {component_id}")
                    if kind == "replace" and component_id not in components:
                        raise SurfaceValidationError(f"component not found: {component_id}")
                    components[component_id] = value
                    if component_id not in order:
                        order.append(component_id)
                elif kind == "remove" and target.startswith("component:"):
                    component_id = target.split(":", 1)[1]
                    if component_id == spec["root"]:
                        raise SurfaceValidationError("root component cannot be removed")
                    components.pop(component_id, None)
                    order = [item for item in order if item != component_id]
                    for item in components.values():
                        item["children"] = [
                            child
                            for child in item.get("children", [])
                            if child != component_id
                        ]
                elif kind == "state.set":
                    if not target or len(target) > 128:
                        raise SurfaceValidationError("state.set target must be a bounded key")
                    spec["state"][target] = value
                elif kind == "state.remove":
                    spec["state"].pop(target, None)
                else:
                    raise SurfaceValidationError(
                        f"unsupported surface patch operation: {kind}"
                    )

            spec["components"] = [
                components[item] for item in order if item in components
            ]
            spec["revision"] = current.revision + 1
            snapshot = self._validate_graph(spec)
            self._items[surface_id] = snapshot
            return snapshot
