from dataclasses import dataclass, field
from typing import Dict
@dataclass(frozen=True)
class CompactionResult:
    id: str
    conversation_id: str
    entry_id: str
    summary: str
    tokens_before: int
    tokens_after: int
    valid: bool
    validation: Dict[str,object]=field(default_factory=dict)
