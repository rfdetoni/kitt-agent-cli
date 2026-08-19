from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from typing import Optional


@dataclass(frozen=True)
class TraceContext:
    trace_id: str
    workspace_id: str
    conversation_id: str
    turn_id: Optional[str] = None
    goal_id: Optional[str] = None
    child_id: Optional[str] = None
    parent_trace_id: Optional[str] = None

    @classmethod
    def root(cls, workspace_id: str, conversation_id: str) -> "TraceContext":
        return cls(uuid.uuid4().hex, workspace_id, conversation_id)

    def child(self, *, turn_id=None, goal_id=None, child_id=None) -> "TraceContext":
        return TraceContext(
            trace_id=uuid.uuid4().hex,
            workspace_id=self.workspace_id,
            conversation_id=self.conversation_id,
            turn_id=turn_id,
            goal_id=goal_id,
            child_id=child_id,
            parent_trace_id=self.trace_id,
        )
