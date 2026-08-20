from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import Any, FrozenSet, Iterable, Optional
import uuid

from kitt.security.capabilities import canonicalize_capabilities, compute_child_privileges


def _normalize_relative_scope_path(value: str) -> str:
    raw = str(value or ".").replace("\\", "/").strip()
    if raw in {"", ".", "./"}:
        return "."
    while raw.startswith("./"):
        raw = raw[2:]
    path = PurePosixPath(raw or ".")
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Invalid scoped path: {value!r}")
    normalized = path.as_posix()
    return normalized or "."


def _scope_contains(scope: str, path: str) -> bool:
    if scope == ".":
        return True
    return path == scope or path.startswith(f"{scope}/")


def _intersect_path_scopes(
    parent_scope: Optional[Iterable[str]],
    requested_scope: Optional[Iterable[str]],
) -> Optional[FrozenSet[str]]:
    parent = None if parent_scope is None else {
        _normalize_relative_scope_path(path) for path in parent_scope
    }
    requested = None if requested_scope is None else {
        _normalize_relative_scope_path(path) for path in requested_scope
    }
    if parent is not None and "." in parent:
        parent = None
    if requested is not None and "." in requested:
        requested = None

    if parent is None:
        return None if requested is None else frozenset(requested)
    if requested is None:
        return frozenset(parent)

    intersections: set[str] = set()
    for parent_path in parent:
        for requested_path in requested:
            if _scope_contains(parent_path, requested_path):
                intersections.add(requested_path)
            elif _scope_contains(requested_path, parent_path):
                intersections.add(parent_path)

    if not intersections:
        return frozenset()

    # Keep only the broadest entries so equivalent nested scopes are not stored twice.
    reduced = {
        candidate
        for candidate in intersections
        if not any(
            other != candidate and _scope_contains(other, candidate)
            for other in intersections
        )
    }
    return frozenset(reduced)


@dataclass(frozen=True)
class ExecutionSecurityContext:
    """Immutable execution principal with capability and optional path scope.

    ``path_scope=None`` means the principal may access any path inside the
    workspace, subject to capabilities and WorkspacePathPolicy. A concrete
    ``path_scope`` is an additional restriction and can only be narrowed by
    descendants.
    """

    workspace_id: str
    conversation_id: str
    turn_id: str
    origin: str
    principal_type: str
    principal_id: str
    capabilities: FrozenSet[str]
    trace_id: str
    parent_principal_id: Optional[str] = None
    path_scope: Optional[FrozenSet[str]] = None
    approval_integrity: Optional[dict[str, Optional[str]]] = None

    def has_capability(self, capability: str) -> bool:
        return capability in self.capabilities

    def check_capability(self, capability: str) -> None:
        if capability not in self.capabilities:
            raise PermissionError(
                f"Capability '{capability}' is not granted to "
                f"{self.principal_type}:{self.principal_id}"
            )

    @property
    def is_path_scoped(self) -> bool:
        return self.path_scope is not None

    def allows_path(self, relative_path: str) -> bool:
        if self.path_scope is None:
            return True
        path = _normalize_relative_scope_path(relative_path)
        return any(_scope_contains(scope, path) for scope in self.path_scope)

    def is_ancestor_of_allowed_path(self, relative_path: str) -> bool:
        if self.path_scope is None:
            return True
        path = _normalize_relative_scope_path(relative_path)
        return any(_scope_contains(path, scope) for scope in self.path_scope)

    def assert_path_allowed(self, relative_path: str) -> None:
        if not self.allows_path(relative_path):
            raise PermissionError(
                f"Path '{relative_path}' is outside the principal path scope"
            )

    def assert_scope(self, workspace_id: str, conversation_id: str) -> None:
        if self.workspace_id != workspace_id:
            raise PermissionError("Cross-workspace execution blocked")
        if self.conversation_id != conversation_id:
            raise PermissionError("Cross-conversation execution blocked")

    def with_turn(self, turn_id: str) -> ExecutionSecurityContext:
        return replace(self, turn_id=turn_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "origin": self.origin,
            "principal_type": self.principal_type,
            "principal_id": self.principal_id,
            "capabilities": sorted(self.capabilities),
            "trace_id": self.trace_id,
            "parent_principal_id": self.parent_principal_id,
            "path_scope": None if self.path_scope is None else sorted(self.path_scope),
            "approval_integrity": (
                None if self.approval_integrity is None else dict(self.approval_integrity)
            ),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ExecutionSecurityContext:
        if not isinstance(payload, dict):
            raise ValueError("Security context payload must be an object")
        caps = frozenset(canonicalize_capabilities(payload.get("capabilities", [])))
        raw_scope = payload.get("path_scope")
        normalized_scope = (
            None
            if raw_scope is None
            else frozenset(_normalize_relative_scope_path(path) for path in raw_scope)
        )
        path_scope = None if normalized_scope and "." in normalized_scope else normalized_scope
        return cls(
            workspace_id=str(payload["workspace_id"]),
            conversation_id=str(payload["conversation_id"]),
            turn_id=str(payload.get("turn_id", "")),
            origin=str(payload.get("origin", "UNKNOWN")),
            principal_type=str(payload.get("principal_type", "UNKNOWN")),
            principal_id=str(payload.get("principal_id", "unknown")),
            capabilities=caps,
            trace_id=str(payload.get("trace_id") or uuid.uuid4().hex),
            parent_principal_id=payload.get("parent_principal_id"),
            path_scope=path_scope,
            approval_integrity=(
                dict(payload["approval_integrity"])
                if isinstance(payload.get("approval_integrity"), dict)
                else None
            ),
        )

    @classmethod
    def create_user_context(
        cls,
        workspace_id: str,
        conversation_id: str,
        turn_id: str = "",
        capabilities: Optional[Iterable[str]] = None,
        trace_id: Optional[str] = None,
        path_scope: Optional[Iterable[str]] = None,
    ) -> ExecutionSecurityContext:
        caps = frozenset(canonicalize_capabilities(capabilities or ()))
        normalized_scope = (
            None
            if path_scope is None
            else frozenset(_normalize_relative_scope_path(path) for path in path_scope)
        )
        if normalized_scope and "." in normalized_scope:
            normalized_scope = None
        return cls(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            origin="USER",
            principal_type="USER",
            principal_id="user_root",
            capabilities=caps,
            trace_id=trace_id or uuid.uuid4().hex,
            path_scope=normalized_scope,
        )

    def derive_skill_context(
        self,
        skill_id: str,
        requested_capabilities: Iterable[str],
        turn_id: Optional[str] = None,
        allowed_paths: Optional[Iterable[str]] = None,
    ) -> ExecutionSecurityContext:
        """Derive a non-escalating executable-skill principal.

        Skills inherit the parent's workspace/conversation and can only narrow
        both capabilities and path scope. An omitted skill path request keeps
        the parent's path scope unchanged.
        """
        requested_caps = canonicalize_capabilities(requested_capabilities)
        skill_caps = requested_caps & set(self.capabilities)
        requested_scope = None if allowed_paths is None else list(allowed_paths)
        skill_scope = _intersect_path_scopes(self.path_scope, requested_scope)
        if requested_scope is not None and skill_scope == frozenset():
            raise PermissionError("Skill requested paths outside parent path scope")

        return ExecutionSecurityContext(
            workspace_id=self.workspace_id,
            conversation_id=self.conversation_id,
            turn_id=turn_id or self.turn_id,
            origin="SKILL",
            principal_type="SKILL",
            principal_id=skill_id,
            capabilities=frozenset(skill_caps),
            trace_id=self.trace_id,
            parent_principal_id=self.principal_id,
            path_scope=skill_scope,
        )

    def derive_child_context(
        self,
        child_id: str,
        requested_capabilities: Iterable[str],
        turn_id: Optional[str] = None,
        workspace_policy_caps: Optional[Iterable[str]] = None,
        autonomy_policy_caps: Optional[Iterable[str]] = None,
        allowed_paths: Optional[Iterable[str]] = None,
    ) -> ExecutionSecurityContext:
        child_caps = compute_child_privileges(
            requested=requested_capabilities,
            parent_capabilities=self.capabilities,
            policy_allowed=workspace_policy_caps,
        )
        if autonomy_policy_caps is not None:
            child_caps &= canonicalize_capabilities(autonomy_policy_caps)

        requested_paths = None if allowed_paths is None else list(allowed_paths)
        requested_scope = None if not requested_paths else requested_paths
        child_scope = _intersect_path_scopes(self.path_scope, requested_scope)
        if requested_scope is not None and child_scope == frozenset():
            raise PermissionError("Child requested paths outside parent path scope")

        return ExecutionSecurityContext(
            workspace_id=self.workspace_id,
            conversation_id=self.conversation_id,
            turn_id=turn_id or self.turn_id,
            origin="AGENT",
            principal_type="CHILD",
            principal_id=child_id,
            capabilities=frozenset(child_caps),
            trace_id=self.trace_id,
            parent_principal_id=self.principal_id,
            path_scope=child_scope,
        )
