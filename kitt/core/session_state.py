from dataclasses import dataclass, field
from typing import List, Dict, Set, Optional
from kitt.domain.entities import SemanticTask, ContextPlan, ChangeSet

@dataclass
class SessionState:
    """Session state manager preventing full history replay."""
    current_prompt: str = ""
    last_task: Optional[SemanticTask] = None
    last_plan: Optional[ContextPlan] = None
    confirmed_decisions: List[str] = field(default_factory=list)
    read_files: Set[str] = field(default_factory=set)
    modified_files: Set[str] = field(default_factory=set)
    last_changeset: Optional[ChangeSet] = None
    last_validation_error: Optional[str] = None
    compact_history_summary: str = ""
