import uuid
from dataclasses import dataclass, field
from typing import Set, Optional
from kitt.tools.approval import ApprovalGrant

@dataclass
class TurnCommand:
    """Command payload representing a single turn execution request."""
    conversation_id: str
    prompt: str
    mode: str = "auto"  # ask | plan | code | auto | review
    explicit_files: Set[str] = field(default_factory=set)
    no_history: bool = False
    dry_run: bool = False
    approval_grant: Optional[ApprovalGrant] = None
    turn_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
