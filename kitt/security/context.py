from __future__ import annotations

from dataclasses import dataclass, replace
from typing import FrozenSet, Iterable, Optional, Any
import uuid

from kitt.security.capabilities import canonicalize_capabilities, compute_child_privileges


@dataclass(frozen=True)
class ExecutionSecurityContext:
    """Immutable, scoped execution principal.

    Security is fail-closed: a context created without capabilities has no
    privileges. Callers must explicitly derive/grant the permissions required
    by the current turn, goal, child or skill.
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

    def has_capability(self, capability: str) -> bool:
        return capability in self.capabilities

    def check_capability(self, capability: str) -> None:
        if capability not in self.capabilities:
            raise PermissionError(
                f"Capability '{capability}' is not granted to "
                f"{self.principal_type}:{self.principal_id}"
            )

    def assert_scope(self, workspace_id: str, conversation_id: str) -> None:
        if self.workspace_id != workspace_id:
            raise PermissionError("Cross-workspace execution blocked")
        if self.conversation_id != conversation_id:
            raise PermissionError("Cross-conversation execution blocked")

    def with_turn(self, turn_id: str) -> "ExecutionSecurityContext":
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
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ExecutionSecurityContext":
        if not isinstance(payload, dict):
            raise ValueError("Security context payload must be an object")
        caps = frozenset(canonicalize_capabilities(payload.get("capabilities", [])))
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
        )

    @classmethod
    def create_user_context(
        cls,
        workspace_id: str,
        conversation_id: str,
        turn_id: str = "",
        capabilities: Optional[Iterable[str]] = None,
        trace_id: Optional[str] = None,
    ) -> "ExecutionSecurityContext":
        # Deliberately fail closed. None means no privileges, not ALL.
        caps = frozenset(canonicalize_capabilities(capabilities or ()))
        return cls(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            origin="USER",
            principal_type="USER",
            principal_id="user_root",
            capabilities=caps,
            trace_id=trace_id or uuid.uuid4().hex,
        )

    def derive_child_context(
        self,
        child_id: str,
        requested_capabilities: Iterable[str],
        turn_id: Optional[str] = None,
        workspace_policy_caps: Optional[Iterable[str]] = None,
        autonomy_policy_caps: Optional[Iterable[str]] = None,
    ) -> "ExecutionSecurityContext":
        child_caps = compute_child_privileges(
            requested=requested_capabilities,
            parent_capabilities=self.capabilities,
            policy_allowed=workspace_policy_caps,
        )
        if autonomy_policy_caps is not None:
            child_caps &= canonicalize_capabilities(autonomy_policy_caps)
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
        )
