import uuid
import time
import hashlib
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, Set

@dataclass
class ApprovalGrant:
    """Single-use authorization grant issued exclusively by user interaction layer."""
    approval_id: str
    turn_id: str
    action_hash: str
    granted_at: float = field(default_factory=time.time)
    nonce: str = field(default_factory=lambda: uuid.uuid4().hex)

class ApprovalManager:
    """Manages emission and validation of single-use user approval grants."""

    def __init__(self, ttl_seconds: float = 300.0):
        self.ttl_seconds = ttl_seconds
        self.used_nonces: Set[str] = set()

    def issue_grant(self, turn_id: str, action_hash: str) -> ApprovalGrant:
        return ApprovalGrant(
            approval_id=uuid.uuid4().hex[:12],
            turn_id=turn_id,
            action_hash=action_hash,
            granted_at=time.time(),
            nonce=uuid.uuid4().hex
        )

    def validate_and_consume(self, grant: Optional[ApprovalGrant], expected_action_hash: str) -> bool:
        if not grant:
            return False

        if grant.nonce in self.used_nonces:
            return False  # Reused token

        if time.time() - grant.granted_at > self.ttl_seconds:
            return False  # Expired token

        if grant.action_hash != expected_action_hash:
            return False  # Mismatched action

        self.used_nonces.add(grant.nonce)
        return True
