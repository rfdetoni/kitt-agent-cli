"""Immutable runtime operation catalog.

The model-facing runtime is intentionally compact, but the authority metadata
behind each operation must not depend on import order or module side effects.
"""
from __future__ import annotations

from collections.abc import Iterator, Mapping
from types import MappingProxyType
from typing import Generic, TypeVar


SpecT = TypeVar("SpecT")


class RuntimeOperationRegistry(Mapping[str, SpecT], Generic[SpecT]):
    """Read-only operation registry with explicit, copy-on-extend composition."""

    __slots__ = ("_specs",)

    def __init__(self, specs: Mapping[str, SpecT]):
        normalized = dict(specs)
        for name, spec in normalized.items():
            if not isinstance(name, str) or not name or name.strip() != name:
                raise ValueError(f"Invalid runtime operation name: {name!r}")
            declared_name = getattr(spec, "name", name)
            if declared_name != name:
                raise ValueError(
                    f"Runtime operation key {name!r} does not match spec name "
                    f"{declared_name!r}"
                )
        self._specs = MappingProxyType(normalized)

    def __getitem__(self, key: str) -> SpecT:
        return self._specs[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._specs)

    def __len__(self) -> int:
        return len(self._specs)

    def extend(self, specs: Mapping[str, SpecT]) -> "RuntimeOperationRegistry[SpecT]":
        additions = dict(specs)
        duplicates = sorted(set(self._specs).intersection(additions))
        if duplicates:
            raise ValueError(
                "Runtime operation(s) already registered: " + ", ".join(duplicates)
            )
        merged = dict(self._specs)
        merged.update(additions)
        return type(self)(merged)

    def snapshot(self) -> Mapping[str, SpecT]:
        """Return the same immutable mapping used internally."""
        return self._specs
