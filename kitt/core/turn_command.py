import uuid
from dataclasses import dataclass, field
from typing import Set, Optional, Any
from kitt.tools.approval import ApprovalGrant
from kitt.llm.attachments import is_binary_attachment_path


@dataclass
class TurnCommand:
    """Command payload representing a single scoped turn execution request."""
    conversation_id: str
    prompt: str
    mode: str = "auto"
    explicit_files: Set[str] = field(default_factory=set)
    no_history: bool = False
    dry_run: bool = False
    approval_grant: Optional[ApprovalGrant] = None
    security_context: Optional[Any] = None
    turn_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    attachments: Set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        explicit = set(self.explicit_files or ())
        attachments = set(self.attachments or ())
        implicit = {path for path in explicit if is_binary_attachment_path(path)}
        self.explicit_files = explicit - implicit
        self.attachments = attachments | implicit

