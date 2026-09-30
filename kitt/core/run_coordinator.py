from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Any

from kitt.native.coordinator import CoordinationConflict, WorkspaceCoordinator


RUN_STATES = {
    "IDLE",
    "RUNNING",
    "STOPPING",
    "FOLLOWUP_PENDING",
    "PAUSED",
    "FAILED",
}

_ALLOWED_TRANSITIONS = {
    "IDLE": {"RUNNING"},
    "RUNNING": {"STOPPING", "FOLLOWUP_PENDING", "PAUSED", "FAILED", "IDLE"},
    "STOPPING": {"FAILED", "IDLE"},
    "FOLLOWUP_PENDING": {"RUNNING", "STOPPING", "PAUSED", "FAILED", "IDLE"},
    "PAUSED": {"RUNNING", "STOPPING", "FAILED", "IDLE"},
    "FAILED": {"RUNNING", "IDLE"},
}

_MUTATING_RUNTIME = {
    "repo.edit_symbol",
    "repo.write_file",
    "repo.create_directory",
    "repo.move",
    "repo.rename",
    "repo.delete",
    "patch.apply",
    "process.run",
    "flow.execute",
    "program.execute",
    "state.set",
}
_MUTATING_TOOLS = {
    "apply_patch",
    "write_file",
    "create_directory",
    "move",
    "rename",
    "delete",
    "run_command",
}


@dataclass(frozen=True)
class RunSnapshot:
    conversation_id: str
    turn_id: str
    state: str
    sequence: int = 0
    reason: str = ""


class RunCoordinator:
    """Single owner for run-state transitions and workspace mutation leases.

    State changes are appended to the durable EventLedger before callers are
    allowed to observe the new state. Workspace resource locking is delegated to
    the existing FIFO WorkspaceCoordinator rather than reimplemented here.
    """

    def __init__(
        self,
        ledger,
        *,
        workspace_coordinator: WorkspaceCoordinator | None = None,
    ) -> None:
        self.ledger = ledger
        self.workspace = workspace_coordinator
        self._lock = threading.RLock()
        self._states: dict[tuple[str, str], RunSnapshot] = {}

    def state(self, conversation_id: str, turn_id: str) -> RunSnapshot:
        key = (conversation_id, turn_id)
        with self._lock:
            cached = self._states.get(key)
        if cached is not None:
            return cached
        latest = None
        for event in self.ledger.events(
            conversation_id,
            turn_id=turn_id,
            limit=10000,
        ):
            if event.event_type == "RunStateChanged":
                latest = RunSnapshot(
                    conversation_id,
                    turn_id,
                    str(event.payload.get("state") or "IDLE"),
                    event.sequence,
                    str(event.payload.get("reason") or ""),
                )
        snapshot = latest or RunSnapshot(conversation_id, turn_id, "IDLE")
        with self._lock:
            self._states[key] = snapshot
        return snapshot

    def transition(
        self,
        conversation_id: str,
        turn_id: str,
        state: str,
        *,
        reason: str = "",
    ) -> RunSnapshot:
        target = str(state or "").upper()
        if target not in RUN_STATES:
            raise ValueError(f"unknown run state: {state}")
        with self._lock:
            current = self.state(conversation_id, turn_id)
            if target == current.state:
                return current
            if target not in _ALLOWED_TRANSITIONS.get(current.state, set()):
                raise RuntimeError(
                    f"invalid run transition {current.state}->{target}"
                )
            record = self.ledger.append_event(
                conversation_id,
                "RunStateChanged",
                {
                    "previous": current.state,
                    "state": target,
                    "reason": str(reason or "")[:500],
                },
                turn_id=turn_id,
                source="run-coordinator",
                durability="SYNC",
                replayable=True,
            )
            snapshot = RunSnapshot(
                conversation_id,
                turn_id,
                target,
                record.sequence,
                str(reason or "")[:500],
            )
            self._states[(conversation_id, turn_id)] = snapshot
            return snapshot

    def observe_event(
        self,
        conversation_id: str,
        turn_id: str,
        event_type: str,
    ) -> RunSnapshot:
        mapping = {
            "TurnStarted": "RUNNING",
            "ApprovalRequired": "PAUSED",
            "TurnCompleted": "IDLE",
            "TurnCancelled": "IDLE",
            "TurnFailed": "FAILED",
            "TurnBlocked": "FAILED",
        }
        target = mapping.get(str(event_type))
        if target is None:
            return self.state(conversation_id, turn_id)
        return self.transition(
            conversation_id,
            turn_id,
            target,
            reason=str(event_type),
        )

    @staticmethod
    def _patch_paths(patch: str) -> list[str]:
        paths = []
        for match in re.finditer(
            r"(?m)^(?:\+\+\+|---)\s+(?:[ab]/)?([^\s]+)|^([A-Za-z0-9_.@/\\-]+\.[A-Za-z0-9]+)\s*$",
            str(patch or ""),
        ):
            value = match.group(1) or match.group(2)
            if value and value != "/dev/null":
                paths.append(value.replace("\\", "/"))
        return list(dict.fromkeys(paths))

    @classmethod
    def mutation_paths(cls, tool_name: str, args: dict[str, Any]) -> list[str] | None:
        name = str(tool_name or "")
        payload = args if isinstance(args, dict) else {}
        if name == "kitt_runtime":
            operation = str(payload.get("operation") or "")
            if operation not in _MUTATING_RUNTIME:
                return None
            raw = payload.get("arguments")
            inner = raw if isinstance(raw, dict) else {}
            if operation == "patch.apply":
                return cls._patch_paths(str(inner.get("patch") or "")) or ["."]
            path = inner.get("path") or inner.get("file")
            if isinstance(path, str) and path.strip():
                return [path.strip()]
            return ["."]
        if name not in _MUTATING_TOOLS:
            return None
        if name == "apply_patch":
            return cls._patch_paths(str(payload.get("patch") or "")) or ["."]
        path = payload.get("path") or payload.get("file")
        if isinstance(path, str) and path.strip():
            return [path.strip()]
        return ["."]

    def claim_tool(
        self,
        conversation_id: str,
        turn_id: str,
        tool_name: str,
        args: dict[str, Any],
        *,
        wait_timeout: float = 8.0,
    ) -> list:
        if self.workspace is None:
            return []
        paths = self.mutation_paths(tool_name, args)
        if paths is None:
            return []
        owner = f"turn:{conversation_id}:{turn_id}"
        return self.workspace.claim_paths(
            paths,
            owner,
            f"{tool_name} mutation",
            wait_timeout=wait_timeout,
        )

    def release_tool(
        self,
        conversation_id: str,
        turn_id: str,
    ) -> int:
        if self.workspace is None:
            return 0
        owner = f"turn:{conversation_id}:{turn_id}"
        return self.workspace.release_owner(owner)

    def close(self) -> None:
        if self.workspace is not None:
            self.workspace.gc_expired_leases()


__all__ = [
    "CoordinationConflict",
    "RunCoordinator",
    "RunSnapshot",
]
