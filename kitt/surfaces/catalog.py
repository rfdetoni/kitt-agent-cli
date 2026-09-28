from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ComponentSpec:
    name: str
    required: tuple[str, ...] = ()
    action_props: tuple[str, ...] = ()


CORE_COMPONENTS = (
    ComponentSpec("Text"),
    ComponentSpec("Markdown"),
    ComponentSpec("Heading", ("text",)),
    ComponentSpec("Badge", ("text",)),
    ComponentSpec("Status", ("text",)),
    ComponentSpec("Metric", ("label", "value")),
    ComponentSpec("Progress", ("value",)),
    ComponentSpec("List"),
    ComponentSpec("Table"),
    ComponentSpec("Tree"),
    ComponentSpec("Code"),
    ComponentSpec("Diff"),
    ComponentSpec("Log"),
    ComponentSpec("Alert", ("text",)),
    ComponentSpec("Tabs"),
    ComponentSpec("Input", ("name",)),
    ComponentSpec("Select", ("name",)),
    ComponentSpec("Toggle", ("name",)),
    ComponentSpec("Button", ("label", "action"), ("action",)),
    ComponentSpec("ButtonGroup"),
    ComponentSpec("Artifact", ("artifact_id",)),
    ComponentSpec("Timeline"),
)


class ComponentCatalog:
    def __init__(self, catalog_id: str = "kitt.core.v1", specs=CORE_COMPONENTS):
        self.catalog_id = catalog_id
        self._specs = {spec.name: spec for spec in specs}

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._specs))

    def validate(self, component: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        component_id = str(component.get("id") or "").strip()
        name = str(component.get("component") or "").strip()
        props = component.get("props", {})
        children = component.get("children", [])

        if not component_id or len(component_id) > 128:
            errors.append("component id must be 1..128 characters")
        spec = self._specs.get(name)
        if spec is None:
            errors.append(f"component '{name}' is not registered in {self.catalog_id}")
            return errors
        if not isinstance(props, dict):
            errors.append(f"{component_id}: props must be an object")
            props = {}
        if not isinstance(children, (list, tuple)) or len(children) > 128:
            errors.append(f"{component_id}: children must be an array with at most 128 entries")
        for field in spec.required:
            if field not in props or props[field] in (None, ""):
                errors.append(f"{component_id}: missing required prop '{field}'")
        for field in spec.action_props:
            value = props.get(field)
            if value is not None and (
                not isinstance(value, str)
                or not value.strip()
                or len(value) > 128
                or any(ch.isspace() for ch in value)
            ):
                errors.append(f"{component_id}: invalid semantic action id")
        return errors
