from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, MutableMapping, Optional


RESUME_DESCRIPTOR_KEY = "__kitt_resume_descriptor__"


def canonical_args_digest(args: dict) -> str:
    raw = json.dumps(
        args,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def embed_resume_descriptor(
    envelope_args: MutableMapping[str, Any],
    *,
    tool_name: str,
    arguments: dict,
    affected_paths: Optional[list[str]] = None,
    before_hashes: Optional[dict[str, Optional[str]]] = None,
) -> None:
    """Attach a concrete resume action to a composite-tool request.

    TurnProcessor persists ``envelope_args`` after a tool reports that approval
    is required. PendingAction consumes this descriptor in ``__post_init__`` so
    continuation executes the exact concrete action that was approved instead
    of replaying a broad composite envelope.
    """

    envelope_args[RESUME_DESCRIPTOR_KEY] = {
        "tool_name": str(tool_name),
        "arguments": dict(arguments),
        "affected_paths": list(affected_paths or ()),
        "before_hashes": dict(before_hashes or {}),
    }


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
    before_hashes: dict[str, Optional[str]]
    created_at: float
    expires_at: float
    state: str
    security_context: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        descriptor = self.normalized_args.get(RESUME_DESCRIPTOR_KEY)
        if isinstance(descriptor, dict):
            resume_tool = str(descriptor.get("tool_name") or "").strip()
            resume_args = descriptor.get("arguments")
            if not resume_tool or not isinstance(resume_args, dict):
                raise ValueError("Invalid composite resume descriptor")

            affected_paths = descriptor.get("affected_paths") or []
            before_hashes = descriptor.get("before_hashes") or {}
            if not isinstance(affected_paths, list) or not isinstance(before_hashes, dict):
                raise ValueError("Invalid composite resume integrity metadata")

            concrete_args = dict(resume_args)
            object.__setattr__(self, "tool_name", resume_tool)
            object.__setattr__(self, "normalized_args", concrete_args)
            object.__setattr__(
                self, "affected_paths", [str(path) for path in affected_paths]
            )
            object.__setattr__(
                self,
                "before_hashes",
                {
                    str(path): (None if digest is None else str(digest))
                    for path, digest in before_hashes.items()
                },
            )
            object.__setattr__(
                self,
                "source_response_sha256",
                canonical_args_digest(concrete_args),
            )

        # Legacy direct patch approvals did not record a marker for paths that
        # were absent at approval time. Persist an out-of-band integrity
        # manifest in the security context so ToolRegistry can inject it only
        # after grant validation (therefore it never changes the approved hash).
        if self.tool_name == "apply_patch" and self.affected_paths:
            manifest = {
                str(path): self.before_hashes.get(str(path))
                for path in self.affected_paths
            }
            security = dict(self.security_context or {})
            security["approval_integrity"] = manifest
            object.__setattr__(self, "security_context", security)

    def get_preconditions(self) -> list:
        from kitt.security.mutation_preconditions import MutationPrecondition
        raw = self.security_context.get("mutation_preconditions") if isinstance(self.security_context, dict) else None
        if isinstance(raw, list):
            return [MutationPrecondition.from_dict(item) for item in raw if isinstance(item, dict)]
        preconditions = []
        for path, digest in self.before_hashes.items():
            preconditions.append(
                MutationPrecondition(
                    path=path,
                    expected_exists=(digest is not None),
                    expected_sha256=digest,
                )
            )
        return preconditions
