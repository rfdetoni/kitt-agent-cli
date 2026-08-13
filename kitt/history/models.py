from dataclasses import dataclass
from typing import Literal, Optional, Dict, Any

SessionEntryType = Literal[
    "USER_MESSAGE",
    "ASSISTANT_MESSAGE",
    "TOOL_RESULT",
    "COMPACTION",
    "BRANCH_SUMMARY",
    "MODEL_CHANGE",
    "LABEL",
    "GOAL_EVENT",
    "CUSTOM_STATE",
]

@dataclass(frozen=True)
class SessionEntry:
    id: str
    conversation_id: str
    parent_entry_id: Optional[str]
    turn_id: Optional[str]
    entry_type: SessionEntryType
    payload: Dict[str, Any]
    include_in_context: bool
    generation: int
    created_at: float
    content_hash: str
