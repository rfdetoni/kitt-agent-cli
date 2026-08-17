"""Privacy modes and remote egress manifest generation."""

from __future__ import annotations

import uuid
import time
from dataclasses import dataclass
from typing import Tuple, Dict, Any, Optional


PRIVACY_MODES = {"offline", "local_only", "hybrid_redacted", "cloud_allowed"}


@dataclass(frozen=True)
class EgressManifest:
    manifest_id: str
    provider: str
    host: str
    model: str
    workspace_id: str
    source_types: Tuple[str, ...]
    paths: Tuple[str, ...]
    bytes_out: int
    estimated_tokens: int
    sensitive_categories: Tuple[str, ...]
    redaction_count: int
    reason: str
    policy_version: str
    created_at: str


class EgressPolicy:
    """Evaluates privacy mode restrictions and builds redacted egress manifests."""

    def __init__(self, mode: str = "hybrid_redacted", policy_version: str = "v2.0"):
        if mode not in PRIVACY_MODES:
            raise ValueError(f"Invalid privacy mode {mode!r}")
        self.mode = mode
        self.policy_version = policy_version

    def evaluate_egress(
        self,
        host: str,
        is_local: bool,
        provider: str,
        model: str,
        workspace_id: str,
        bytes_out: int,
        estimated_tokens: int,
        sensitive_categories: Tuple[str, ...] = (),
        redaction_count: int = 0
    ) -> Tuple[bool, Optional[EgressManifest], str]:
        """Check if egress is allowed by policy and produce EgressManifest."""

        if self.mode == "offline":
            return False, None, "Egress denied: privacy mode is 'offline'"

        if self.mode == "local_only" and not is_local:
            return False, None, f"Egress denied: privacy mode 'local_only' prohibits remote host '{host}'"

        manifest = EgressManifest(
            manifest_id=uuid.uuid4().hex[:12],
            provider=provider,
            host=host,
            model=model,
            workspace_id=workspace_id,
            source_types=("prompt", "context"),
            paths=(),
            bytes_out=bytes_out,
            estimated_tokens=estimated_tokens,
            sensitive_categories=sensitive_categories,
            redaction_count=redaction_count,
            reason="Approved by EgressPolicy",
            policy_version=self.policy_version,
            created_at=str(time.time())
        )
        return True, manifest, "ALLOWED"
