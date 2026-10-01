from __future__ import annotations

import threading
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Callable

from kitt_protocol import BudgetLease, ExecutionBudget


class ExecutionBudgetExceeded(RuntimeError):
    pass


@dataclass
class _LeaseState:
    lease: BudgetLease
    token_cap: int
    call_cap: int
    cost_cap: float
    tool_cap: int = 0
    tokens_used: int = 0
    calls_used: int = 0
    cost_used: float = 0.0
    tools_used: int = 0
    settled: bool = False


class ExecutionBudgetLedger:
    """One hard budget shared by every stage participating in a turn.

    Child leases reserve parent capacity up-front, so concurrent subagents cannot
    overspend a wallet simply because they race one another.
    """

    def __init__(
        self,
        budget: ExecutionBudget,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.budget = budget
        self._clock = clock
        self._started = clock()
        self._lock = threading.RLock()
        self.model_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost = 0.0
        # Child usage is tracked separately from direct input/output so parent
        # input/output caps retain their meaning while max_total/max_calls/cost
        # still cover the entire turn tree.
        self.child_tokens = 0
        self.child_model_calls = 0
        self.child_cost = 0.0
        self.child_tool_calls = 0
        self.tool_calls = 0
        self.subagents = 0
        self._leases: dict[str, _LeaseState] = {}
        self._stage_usage: dict[str, dict[str, float | int]] = {}

    @staticmethod
    def _stage_name(stage: str) -> str:
        value = str(stage or "model").strip().lower()
        return value[:64] or "model"

    def _stage(self, stage: str) -> dict[str, float | int]:
        name = self._stage_name(stage)
        return self._stage_usage.setdefault(
            name,
            {
                "model_calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost": 0.0,
                "tool_calls": 0,
                "subagents": 0,
            },
        )

    def _elapsed_ms(self) -> int:
        return max(0, int((self._clock() - self._started) * 1000))

    def _outstanding(self) -> tuple[int, int, float]:
        tokens = calls = 0
        cost = 0.0
        for state in self._leases.values():
            if state.settled:
                continue
            tokens += max(0, state.token_cap - state.tokens_used)
            calls += max(0, state.call_cap - state.calls_used)
            cost += max(0.0, state.cost_cap - state.cost_used)
        return tokens, calls, cost

    def _outstanding_tools(self) -> int:
        return sum(
            max(0, state.tool_cap - state.tools_used)
            for state in self._leases.values()
            if not state.settled
        )

    def _check_duration(self) -> None:
        if self._elapsed_ms() > max(0, int(self.budget.max_duration_ms)):
            raise ExecutionBudgetExceeded("execution duration budget exceeded")

    def _check_tokens(self, next_input: int = 0, next_output: int = 0) -> None:
        reserved_tokens, _, _ = self._outstanding()
        next_input_total = self.input_tokens + max(0, int(next_input))
        next_output_total = self.output_tokens + max(0, int(next_output))
        if next_input_total > self.budget.max_input_tokens:
            raise ExecutionBudgetExceeded("input token budget exceeded")
        if next_output_total > self.budget.max_output_tokens:
            raise ExecutionBudgetExceeded("output token budget exceeded")
        if (
            next_input_total
            + next_output_total
            + self.child_tokens
            + reserved_tokens
            > self.budget.max_total_tokens
        ):
            raise ExecutionBudgetExceeded("total token budget exceeded")

    def reserve_model_call(
        self,
        *,
        input_tokens: int = 0,
        cost: float = 0.0,
        stage: str = "model",
    ) -> None:
        with self._lock:
            self._check_duration()
            _, reserved_calls, reserved_cost = self._outstanding()
            if (
                self.model_calls
                + self.child_model_calls
                + 1
                + reserved_calls
                > self.budget.max_model_calls
            ):
                raise ExecutionBudgetExceeded("model call budget exceeded")
            self._check_tokens(next_input=input_tokens)
            if (
                self.cost
                + self.child_cost
                + max(0.0, float(cost))
                + reserved_cost
                > self.budget.max_cost
            ):
                raise ExecutionBudgetExceeded("execution cost budget exceeded")
            charged_input = max(0, int(input_tokens))
            charged_cost = max(0.0, float(cost))
            self.model_calls += 1
            self.input_tokens += charged_input
            self.cost += charged_cost
            bucket = self._stage(stage)
            bucket["model_calls"] = int(bucket["model_calls"]) + 1
            bucket["input_tokens"] = int(bucket["input_tokens"]) + charged_input
            bucket["cost"] = float(bucket["cost"]) + charged_cost

    def reconcile_model_input(
        self,
        *,
        estimated_tokens: int,
        actual_tokens: int,
        estimated_cost: float = 0.0,
        actual_cost: float = 0.0,
        stage: str = "model",
    ) -> None:
        """Replace one preflight input estimate with provider-observed usage."""
        estimated = max(0, int(estimated_tokens))
        actual = max(0, int(actual_tokens))
        estimated_usd = max(0.0, float(estimated_cost))
        actual_usd = max(0.0, float(actual_cost))
        with self._lock:
            self._check_duration()
            current_without_estimate = max(0, self.input_tokens - estimated)
            next_total = current_without_estimate + actual
            delta = next_total - self.input_tokens
            if delta > 0:
                self._check_tokens(next_input=delta)

            current_cost_without_estimate = max(
                0.0,
                self.cost - estimated_usd,
            )
            next_cost = current_cost_without_estimate + actual_usd
            _, _, reserved_cost = self._outstanding()
            if next_cost + self.child_cost + reserved_cost > self.budget.max_cost:
                raise ExecutionBudgetExceeded("execution cost budget exceeded")

            self.input_tokens = next_total
            self.cost = next_cost
            bucket = self._stage(stage)
            bucket["input_tokens"] = max(
                0,
                int(bucket["input_tokens"]) + (actual - estimated),
            )
            bucket["cost"] = max(
                0.0,
                float(bucket["cost"]) + (actual_usd - estimated_usd),
            )

    def record_model_output(
        self,
        *,
        output_tokens: int = 0,
        cost: float = 0.0,
        stage: str = "model",
    ) -> None:
        with self._lock:
            self._check_duration()
            self._check_tokens(next_output=output_tokens)
            _, _, reserved_cost = self._outstanding()
            if (
                self.cost
                + self.child_cost
                + max(0.0, float(cost))
                + reserved_cost
                > self.budget.max_cost
            ):
                raise ExecutionBudgetExceeded("execution cost budget exceeded")
            charged_output = max(0, int(output_tokens))
            charged_cost = max(0.0, float(cost))
            self.output_tokens += charged_output
            self.cost += charged_cost
            bucket = self._stage(stage)
            bucket["output_tokens"] = int(bucket["output_tokens"]) + charged_output
            bucket["cost"] = float(bucket["cost"]) + charged_cost

    def reserve_tool_call(self, *, stage: str = "tools") -> None:
        with self._lock:
            self._check_duration()
            if (
                self.tool_calls
                + self.child_tool_calls
                + 1
                + self._outstanding_tools()
                > self.budget.max_tool_calls
            ):
                raise ExecutionBudgetExceeded("tool call budget exceeded")
            self.tool_calls += 1
            bucket = self._stage(stage)
            bucket["tool_calls"] = int(bucket["tool_calls"]) + 1

    def reserve_subagent(
        self,
        child_agent_id: str,
        *,
        token_cap: int,
        call_cap: int,
        cost_cap: float = 0.0,
        tool_cap: int = 0,
    ) -> BudgetLease:
        child = str(child_agent_id or "").strip()
        if not child:
            raise ValueError("child_agent_id is required")
        with self._lock:
            self._check_duration()
            if self.subagents + 1 > self.budget.max_subagents:
                raise ExecutionBudgetExceeded("subagent budget exceeded")
            reserved_tokens, reserved_calls, reserved_cost = self._outstanding()
            requested_tokens = max(0, int(token_cap))
            requested_calls = max(0, int(call_cap))
            requested_cost = max(0.0, float(cost_cap))
            requested_tools = max(0, int(tool_cap))
            if (
                self.input_tokens
                + self.output_tokens
                + self.child_tokens
                + reserved_tokens
                + requested_tokens
                > self.budget.max_total_tokens
            ):
                raise ExecutionBudgetExceeded("subagent token lease exceeds parent budget")
            if (
                self.model_calls
                + self.child_model_calls
                + reserved_calls
                + requested_calls
                > self.budget.max_model_calls
            ):
                raise ExecutionBudgetExceeded("subagent call lease exceeds parent budget")
            if (
                self.cost
                + self.child_cost
                + reserved_cost
                + requested_cost
                > self.budget.max_cost
            ):
                raise ExecutionBudgetExceeded("subagent cost lease exceeds parent budget")
            if (
                self.tool_calls
                + self.child_tool_calls
                + self._outstanding_tools()
                + requested_tools
                > self.budget.max_tool_calls
            ):
                raise ExecutionBudgetExceeded("subagent tool lease exceeds parent budget")
            lease = BudgetLease(
                id=f"lease_{uuid.uuid4().hex}",
                parent_budget_id=f"turn-budget:{id(self)}",
                child_agent_id=child,
                token_cap=requested_tokens,
                call_cap=requested_calls,
                cost_cap=requested_cost,
                reserved={
                    "tokens": requested_tokens,
                    "calls": requested_calls,
                    "cost": requested_cost,
                    "tools": requested_tools,
                },
                consumed={"tokens": 0, "calls": 0, "cost": 0.0, "tools": 0},
            )
            self._leases[lease.id] = _LeaseState(
                lease=lease,
                token_cap=requested_tokens,
                call_cap=requested_calls,
                cost_cap=requested_cost,
                tool_cap=requested_tools,
            )
            self.subagents += 1
            bucket = self._stage("subagents")
            bucket["subagents"] = int(bucket["subagents"]) + 1
            return lease

    def consume_child(
        self,
        lease_id: str,
        *,
        tokens: int = 0,
        calls: int = 0,
        cost: float = 0.0,
        tools: int = 0,
    ) -> None:
        with self._lock:
            # Settlement is accounting, not a new execution action. It must
            # remain possible after the wall-clock deadline so spent capacity
            # cannot disappear merely because the child finished late.
            state = self._leases.get(str(lease_id))
            if state is None or state.settled:
                raise ExecutionBudgetExceeded("unknown or settled child budget lease")
            next_tokens = state.tokens_used + max(0, int(tokens))
            next_calls = state.calls_used + max(0, int(calls))
            next_cost = state.cost_used + max(0.0, float(cost))
            next_tools = state.tools_used + max(0, int(tools))
            if next_tokens > state.token_cap:
                raise ExecutionBudgetExceeded("child token lease exceeded")
            if next_calls > state.call_cap:
                raise ExecutionBudgetExceeded("child call lease exceeded")
            if next_cost > state.cost_cap:
                raise ExecutionBudgetExceeded("child cost lease exceeded")
            if next_tools > state.tool_cap:
                raise ExecutionBudgetExceeded("child tool lease exceeded")
            token_delta = next_tokens - state.tokens_used
            call_delta = next_calls - state.calls_used
            cost_delta = next_cost - state.cost_used
            tool_delta = next_tools - state.tools_used
            state.tokens_used = next_tokens
            state.calls_used = next_calls
            state.cost_used = next_cost
            state.tools_used = next_tools

            # Move consumed lease capacity into global usage immediately. This
            # keeps reserved + consumed capacity invariant even under concurrent
            # parent/child execution.
            self.child_tokens += token_delta
            self.child_model_calls += call_delta
            self.child_cost += cost_delta
            self.child_tool_calls += tool_delta
            bucket = self._stage("subagents")
            bucket["model_calls"] = int(bucket["model_calls"]) + call_delta
            bucket["input_tokens"] = int(bucket["input_tokens"]) + token_delta
            bucket["cost"] = float(bucket["cost"]) + cost_delta
            bucket["tool_calls"] = int(bucket["tool_calls"]) + tool_delta

    def settle_child(self, lease_id: str) -> dict:
        with self._lock:
            state = self._leases.get(str(lease_id))
            if state is None:
                raise KeyError(lease_id)
            state.settled = True
            return {
                "lease_id": state.lease.id,
                "child_agent_id": state.lease.child_agent_id,
                "tokens_used": state.tokens_used,
                "calls_used": state.calls_used,
                "cost_used": state.cost_used,
                "tools_used": state.tools_used,
            }

    def check(self) -> None:
        with self._lock:
            self._check_duration()
            self._check_tokens()
            if self.cost + self.child_cost > self.budget.max_cost:
                raise ExecutionBudgetExceeded("execution cost budget exceeded")

    def snapshot(self) -> dict:
        with self._lock:
            reserved_tokens, reserved_calls, reserved_cost = self._outstanding()
            reserved_tools = self._outstanding_tools()
            return {
                "budget": asdict(self.budget),
                "usage": {
                    "model_calls": self.model_calls + self.child_model_calls,
                    "direct_model_calls": self.model_calls,
                    "child_model_calls": self.child_model_calls,
                    "input_tokens": self.input_tokens,
                    "output_tokens": self.output_tokens,
                    "child_tokens": self.child_tokens,
                    "total_tokens": (
                        self.input_tokens
                        + self.output_tokens
                        + self.child_tokens
                    ),
                    "cost": self.cost + self.child_cost,
                    "direct_cost": self.cost,
                    "child_cost": self.child_cost,
                    "tool_calls": self.tool_calls + self.child_tool_calls,
                    "direct_tool_calls": self.tool_calls,
                    "child_tool_calls": self.child_tool_calls,
                    "subagents": self.subagents,
                    "duration_ms": self._elapsed_ms(),
                },
                "reserved": {
                    "tokens": reserved_tokens,
                    "calls": reserved_calls,
                    "cost": reserved_cost,
                    "tools": reserved_tools,
                },
                "stages": {
                    name: dict(values)
                    for name, values in sorted(self._stage_usage.items())
                },
            }
