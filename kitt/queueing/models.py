from dataclasses import dataclass
from typing import Literal, Optional
QueueKind = Literal["STEERING", "FOLLOW_UP"]

@dataclass(frozen=True)
class QueuedInput:
    id: str
    conversation_id: str
    kind: QueueKind
    content: str
    position: int
    status: str
    target_generation: int
    created_at: float
    delivered_at: Optional[float]
    content_hash: str
