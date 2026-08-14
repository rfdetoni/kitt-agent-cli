import time
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List
from kitt.context_filter.semantic_filter import SemanticFilterResult
from kitt.edit_format.applier import EditResult

@dataclass
class TurnEvent:
    timestamp: float = field(default_factory=time.time)

@dataclass
class TurnStarted(TurnEvent):
    turn_id: str = ""
    conversation_id: str = ""
    prompt: str = ""

@dataclass
class FilterCompleted(TurnEvent):
    filter_res: Optional[SemanticFilterResult] = None

@dataclass
class ContextResolved(TurnEvent):
    resolved_count: int = 0

@dataclass
class ContextBuildCompleted(TurnEvent):
    index_generation: int = 0
    index_state: str = ""
    selected_count: int = 0
    rejected_count: int = 0
    total_tokens: int = 0
    coverage: float = 1.0
    degraded: bool = False
    duration_ms: int = 0
    index_scanned: int = 0
    index_updated: int = 0
    index_deleted: int = 0
    freshness: str = ""
    partial_reason: str = ""
    schema_version: str = ""

@dataclass
class BudgetApplied(TurnEvent):
    total_input_tokens: int = 0
    reserved_output_tokens: int = 1200
    window_size: int = 8192

@dataclass
class ModelSelected(TurnEvent):
    profile_name: str = ""
    model: str = ""

@dataclass
class TextDelta(TurnEvent):
    delta: str = ""

@dataclass
class ToolCallProposed(TurnEvent):
    tool_name: str = ""
    args: Dict[str, Any] = field(default_factory=dict)

@dataclass
class ApprovalRequired(TurnEvent):
    turn_id: str = ""
    conversation_id: str = ""
    tool_name: str = ""
    args: Dict[str, Any] = field(default_factory=dict)
    action_hash: str = ""
    approval_request_id: str = ""
    workspace_id: str = ""

@dataclass
class ThinkingStarted(TurnEvent):
    """Emitido quando o modelo começa a 'pensar' antes de produzir texto/tool call."""
    pass

@dataclass
class ThinkingCompleted(TurnEvent):
    duration_ms: int = 0
    tokens: int = 0

@dataclass
class ToolStarted(TurnEvent):
    tool_name: str = ""
    args: Dict[str, Any] = field(default_factory=dict)
    call_id: str = ""

@dataclass
class ToolCompleted(TurnEvent):
    tool_name: str = ""
    success: bool = True
    output: str = ""
    error: Optional[str] = None
    call_id: str = ""
    tokens: int = 0

@dataclass
class EditPreviewReady(TurnEvent):
    files_affected: List[str] = field(default_factory=list)

@dataclass
class EditApplied(TurnEvent):
    applied_files: List[str] = field(default_factory=list)
    created_files: List[str] = field(default_factory=list)

@dataclass
class ValidationCompleted(TurnEvent):
    success: bool = True
    output: str = ""

@dataclass
class MetricsRecorded(TurnEvent):
    input_tokens: int = 0
    output_tokens: int = 0
    saved_tokens: int = 0

@dataclass
class TurnCompleted(TurnEvent):
    response: str = ""
    edit_result: Optional[EditResult] = None

@dataclass
class TurnFailed(TurnEvent):
    error: str = ""

@dataclass
class TurnCancelled(TurnEvent):
    reason: str = ""

@dataclass
class TurnBlocked(TurnEvent):
    reason: str = ""

@dataclass
class ChildAgentSpawned(TurnEvent):
    child_id: str = ""
    name: str = ""
    task: str = ""

@dataclass
class ChildAgentProgress(TurnEvent):
    child_id: str = ""
    status: str = ""
    summary: str = ""
    progress: int = 0

@dataclass
class ChildAgentFinished(TurnEvent):
    child_id: str = ""
    status: str = ""
    error: Optional[str] = None
