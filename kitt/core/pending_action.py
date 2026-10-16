from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class PendingAction:
    id: str
    approval_request_id: str
    turn_id: str
    conversation_id: str
    workspace_id: str
    tool_name: str
    normalized_args: dict
    action_hash: str
    source_response_sha256: str
    affected_paths: list[str]
    before_hashes: dict[str, Optional[str]]
    created_at: float
    # Zero means the user-facing pending action has no automatic timeout.
    # Expiration is only meaningful for issued ApprovalGrant objects.
    expires_at: float
    state: str
    security_context: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Legacy direct patch approvals did not record a marker for paths that
        # were absent at approval time. Persist an out-of-band integrity
        # manifest in the security context so ToolRegistry can inject it only
        # after grant validation (therefore it never changes the approved hash).
        if self.tool_name == "apply_patch" and self.affected_paths:
            manifest = {
                str(path): self.before_hashes.get(str(path))
                for path in self.affected_paths
            }
            security = dict(self.security_context or {})
            security["approval_integrity"] = manifest
            object.__setattr__(self, "security_context", security)

    def get_preconditions(self) -> list:
        from kitt.security.mutation_preconditions import MutationPrecondition
        raw = self.security_context.get("mutation_preconditions") if isinstance(self.security_context, dict) else None
        if isinstance(raw, list):
            return [MutationPrecondition.from_dict(item) for item in raw if isinstance(item, dict)]
        preconditions = []
        for path, digest in self.before_hashes.items():
            preconditions.append(
                MutationPrecondition(
                    path=path,
                    expected_exists=(digest is not None),
                    expected_sha256=digest,
                )
            )
        return preconditions
