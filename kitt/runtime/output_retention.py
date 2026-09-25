from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RetainedOutput:
    preview: str
    artifact_id: str | None
    original_chars: int
    retained: bool


def retain_tool_output(
    raw_output: str,
    *,
    threshold_chars: int,
    artifact_tools: Any,
    workspace_id: str,
    conversation_id: str,
    turn_id: str,
    tool_name: str,
) -> RetainedOutput:
    """Persist the complete raw payload before substituting a bounded locator."""
    text = str(raw_output)
    if len(text) <= max(1, int(threshold_chars)) or artifact_tools is None:
        return RetainedOutput(text, None, len(text), False)
    artifact = artifact_tools.put(
        workspace_id=workspace_id,
        content=text,
        artifact_type="TOOL_OUTPUT",
        summary=f"Large output from tool {tool_name}",
        conversation_id=conversation_id,
        turn_id=turn_id,
    )
    preview = (
        f"[Large tool output saved to Artifact ID {artifact.id} "
        f"({len(text)} chars). Use artifact_read to inspect.]"
    )
    return RetainedOutput(preview, artifact.id, len(text), True)
