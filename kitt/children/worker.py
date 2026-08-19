from __future__ import annotations

import json
import sys

from kitt.core.runtime import KittRuntime
from kitt.core.turn_command import TurnCommand
from kitt.core.turn_events import TextDelta, TurnCompleted, TurnFailed, TurnBlocked, ApprovalRequired
from kitt.security.context import ExecutionSecurityContext


PREFIX = "KITT_CHILD_RESULT:"


def main() -> None:
    req = json.loads(sys.stdin.read())
    root = req["root"]
    runtime = KittRuntime.build(root)
    try:
        child_conv = req["runtime_conversation_id"]
        conv = runtime.history.repo.get_conversation(child_conv)
        if not conv or conv.get("workspace_id") != runtime.workspace_id:
            raise PermissionError("Retained child conversation is missing or outside workspace")

        sec = ExecutionSecurityContext.from_dict(req["security_context"])
        # Scope the retained specialist to its own persistent conversation.
        sec = ExecutionSecurityContext(
            workspace_id=sec.workspace_id,
            conversation_id=child_conv,
            turn_id="",
            origin="AGENT",
            principal_type="CHILD",
            principal_id=req["child_id"],
            capabilities=sec.capabilities,
            trace_id=sec.trace_id,
            parent_principal_id=sec.parent_principal_id,
        )
        cmd = TurnCommand(
            conversation_id=child_conv,
            prompt=req["task"],
            mode="auto",
            explicit_files=set(req.get("allowed_paths", [])),
            security_context=sec,
        )
        chunks = []
        final = ""
        tokens = 0
        runtime.history.repo.save_message(child_conv, cmd.turn_id, "user", req["task"])
        for event in runtime.processor.run_turn(cmd):
            if isinstance(event, TextDelta):
                chunks.append(event.delta)
            elif isinstance(event, TurnCompleted):
                final = event.response or "".join(chunks)
                if final:
                    runtime.history.repo.save_message(child_conv, cmd.turn_id, "assistant", final)
            elif isinstance(event, ApprovalRequired):
                print(PREFIX + json.dumps({
                    "success": False,
                    "state": "WAITING_APPROVAL",
                    "error": "Child operation requires parent/user approval",
                    "approval_id": event.approval_request_id,
                }))
                return
            elif isinstance(event, (TurnFailed, TurnBlocked)):
                print(PREFIX + json.dumps({
                    "success": False, "state": "FAILED",
                    "error": getattr(event, "error", getattr(event, "reason", "child failed")),
                }))
                return
        print(PREFIX + json.dumps({
            "success": True, "state": "COMPLETED",
            "output": final or "".join(chunks), "tokens_used": tokens,
        }, ensure_ascii=False))
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
