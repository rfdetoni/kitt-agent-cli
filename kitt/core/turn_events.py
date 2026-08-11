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
    tool_name: str = ""
    args: Dict[str, Any] = field(default_factory=dict)
    action_hash: str = ""

@dataclass
class ToolStarted(TurnEvent):
    tool_name: str = ""

@dataclass
class ToolCompleted(TurnEvent):
    tool_name: str = ""
    success: bool = True
    output: str = ""
    error: Optional[str] = None

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
