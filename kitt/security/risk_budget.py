from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass
import threading


_READ_ONLY_TOOLS = frozenset({
    "kitt_runtime",
    "list_files",
    "search",
    "read_file",
    "repository_map",
    "git_status",
    "git_diff",
    "python_compute",
    "artifact_list",
    "artifact_read",
})

_TOOL_RISK_COSTS = {
    "create_directory": 1,
    "write_file": 1,
    "apply_patch": 1,
    "artifact_store": 1,
    "queue_input": 1,
    "goal_add_gate": 1,
    "harness_remember": 1,
    "goal_create": 2,
    "child_spawn": 2,
    "run_command": 3,
}


def tool_risk_cost(tool_name: str) -> int:
    name = str(tool_name or "").strip()
    if not name or name in _READ_ONLY_TOOLS:
        return 0
    return int(_TOOL_RISK_COSTS.get(name, 5))


def is_automatic_origin(origin: str) -> bool:
    return str(origin or "MODEL").upper() not in {
        "USER",
        "UI",
        "SAFE_RUNTIME_BROKER",
    }


@dataclass(frozen=True)
class RiskBudgetReservation:
    allowed: bool
    reserved: bool
    turn_id: str
    conversation_id: str
    risk_cost: int
    actions_used: int
    risk_used: int
    max_actions: int
    max_risk: int
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class RiskBudgetLedger:
    """Thread-safe per-turn budget for automatic privileged actions."""

    def __init__(self, max_tracked_turns: int = 256):
        self.max_tracked_turns = max(16, int(max_tracked_turns))
        self._usage: OrderedDict[tuple[str, str], tuple[int, int]] = OrderedDict()
        self._lock = threading.RLock()

    def reserve(
        self,
        *,
        turn_id: str,
        conversation_id: str,
        risk_cost: int,
        max_actions: int,
        max_risk: int,
    ) -> RiskBudgetReservation:
        turn = str(turn_id or "default_turn")
        conversation = str(conversation_id or "default_conv")
        cost = max(0, int(risk_cost))
        action_limit = max(0, int(max_actions))
        risk_limit = max(0, int(max_risk))
        if cost == 0:
            return RiskBudgetReservation(
                True, False, turn, conversation, 0, 0, 0,
                action_limit, risk_limit, "zero-risk",
            )

        key = (conversation, turn)
        with self._lock:
            actions, risk = self._usage.get(key, (0, 0))
            next_actions = actions + 1
            next_risk = risk + cost
            if next_actions > action_limit:
                return RiskBudgetReservation(
                    False, False, turn, conversation, cost, actions, risk,
                    action_limit, risk_limit, "action-limit",
                )
            if next_risk > risk_limit:
                return RiskBudgetReservation(
                    False, False, turn, conversation, cost, actions, risk,
                    action_limit, risk_limit, "risk-limit",
                )

            self._usage[key] = (next_actions, next_risk)
            self._usage.move_to_end(key)
            while len(self._usage) > self.max_tracked_turns:
                self._usage.popitem(last=False)
            return RiskBudgetReservation(
                True, True, turn, conversation, cost, next_actions, next_risk,
                action_limit, risk_limit, "reserved",
            )
