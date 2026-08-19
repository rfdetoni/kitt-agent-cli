from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, FrozenSet, Iterable
from kitt.security.capabilities import canonicalize_capabilities, ALL_CAPABILITIES


@dataclass(frozen=True)
class ExecutionSecurityContext:
    """Security context bounding an execution principal (user, child agent, skill, goal)."""
    workspace_id: str
    conversation_id: str
    turn_id: str
    origin: str  # e.g., "USER", "AGENT", "SCHEDULE", "SKILL", "DAEMON"
    principal_type: str  # "USER", "CHILD", "SKILL", "GOAL"
    principal_id: str
    capabilities: FrozenSet[str]
    trace_id: str
    parent_principal_id: Optional[str] = None

    def has_capability(self, capability: str) -> bool:
        """Check if the given capability is granted in this context."""
        return capability in self.capabilities

    def check_capability(self, capability: str) -> None:
        """Enforce that the given capability is granted. Raises PermissionError if missing."""
        if not self.has_capability(capability):
            raise PermissionError(
                f"Capability '{capability}' is required for operation but not granted to "
                f"principal '{self.principal_type}:{self.principal_id}' in workspace '{self.workspace_id}'"
            )

    @classmethod
    def create_user_context(
        cls,
        workspace_id: str,
        conversation_id: str,
        turn_id: str,
        capabilities: Optional[Iterable[str]] = None,
        trace_id: Optional[str] = None,
    ) -> ExecutionSecurityContext:
        """Create a default root security context for the user."""
        import uuid
        caps = frozenset(canonicalize_capabilities(capabilities)) if capabilities is not None else ALL_CAPABILITIES
        return cls(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            origin="USER",
            principal_type="USER",
            principal_id="user_root",
            capabilities=caps,
            trace_id=trace_id or str(uuid.uuid4()),
        )

    def derive_child_context(
        self,
        child_id: str,
        requested_capabilities: Iterable[str],
        turn_id: Optional[str] = None,
        workspace_policy_caps: Optional[Iterable[str]] = None,
    ) -> ExecutionSecurityContext:
        """Derive a restricted child security context using strict intersection rule."""
        from kitt.security.capabilities import compute_child_privileges
        child_caps = frozenset(compute_child_privileges(
            requested=requested_capabilities,
            parent_capabilities=self.capabilities,
            policy_allowed=workspace_policy_caps,
        ))
        return ExecutionSecurityContext(
            workspace_id=self.workspace_id,
            conversation_id=self.conversation_id,
            turn_id=turn_id or self.turn_id,
            origin="AGENT",
            principal_type="CHILD",
            principal_id=child_id,
            capabilities=child_caps,
            trace_id=self.trace_id,
            parent_principal_id=self.principal_id,
        )
