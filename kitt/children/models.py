from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class ChildSession:
    id: str
    parent_conversation_id: str
    parent_turn_id: str
    name: str
    task: str
    state: str
    depth: int
    model_profile: str
    allowed_paths: List[str] = field(default_factory=list)
    enabled_tools: List[str] = field(default_factory=list)
    token_budget: int = 0
    tokens_used: int = 0
    timeout_seconds: int = 120
    result_artifact_id: Optional[str] = None
    error: Optional[str] = None
    created_at: float = 0
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    current_task_id: Optional[str] = None
    task_started_at: Optional[float] = None
    capabilities: List[str] = field(default_factory=list)
    context_summary: str = ""
    runtime_conversation_id: Optional[str] = None
