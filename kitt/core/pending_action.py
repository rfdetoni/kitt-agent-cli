import uuid
import time
from dataclasses import dataclass, field
from typing import List, Dict

@dataclass(frozen=True)
class PendingAction:
    id: str
    approval_request_id: str
    turn_id: str
    conversation_id: str
    workspace_id: str
    tool_name: str
    normalized_args: dict
    action_hash: str
    source_response_sha256: str
    affected_paths: list[str]
    before_hashes: dict[str, str]
    created_at: float
    expires_at: float
    state: str
