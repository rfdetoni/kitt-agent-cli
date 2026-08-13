from dataclasses import dataclass, field
from typing import Any, Dict, Optional

@dataclass(frozen=True)
class Artifact:
    id: str
    workspace_id: str
    artifact_type: str
    storage_kind: str
    summary: str
    content_hash: str
    size_bytes: int
    sensitivity: str = "NORMAL"
    conversation_id: Optional[str] = None
    turn_id: Optional[str] = None
    relative_storage_path: Optional[str] = None
    inline_content: Optional[bytes] = None
    created_at: float = 0.0
    expires_at: Optional[float] = None
    pinned: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
