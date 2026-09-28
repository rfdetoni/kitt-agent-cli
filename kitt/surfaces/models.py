from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ComponentNode:
    id: str
    component: str
    props: dict[str, Any] = field(default_factory=dict)
    children: tuple[str, ...] = ()


@dataclass(frozen=True)
class SurfaceSnapshot:
    id: str
    revision: int
    catalog_id: str
    root: str
    components: tuple[ComponentNode, ...]
    state: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "revision": self.revision,
            "catalog_id": self.catalog_id,
            "root": self.root,
            "components": [
                {
                    "id": item.id,
                    "component": item.component,
                    "props": dict(item.props),
                    "children": list(item.children),
                }
                for item in self.components
            ],
            "state": dict(self.state),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class SurfaceCapabilities:
    protocol: str = "surface.v1"
    catalogs: tuple[str, ...] = ("kitt.core.v1",)
    components: tuple[str, ...] = ()
    features: tuple[str, ...] = ("patch", "state", "semantic_actions")
    max_depth: int = 16
    max_nodes: int = 256
