"""KITT-native durable execution, adaptive context, learned routing and verification.

This is a composition adapter over KITT's existing TurnProcessor and ToolRegistry.
It owns no second runtime, scheduler, persistence layer or external framework.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import fields, is_dataclass
from typing import Any

from kitt.core.execution_budget import ExecutionBudgetExceeded, ExecutionBudgetLedger
from kitt.core.runtime_config import RuntimeConfig
from kitt.core.turn_command import TurnCommand
from kitt.core.turn_events import ApprovalRequired, TurnCompleted, TurnFailed
from kitt.evidence.episodes import TaskEpisodeService
from kitt.evidence.invariants import RuntimeInvariantService
from kitt.evidence.ledger import EventLedger
from kitt.evidence.projections import build_default_projection_registry
from kitt.runtime.state import RuntimeStateStore
from kitt.security.authority_snapshot import (
    capture_authority_snapshot,
    validate_authority_snapshot,
)
from kitt.validation.orchestrator import VerificationOrchestrator
from kitt_protocol import ExecutionBudget

TERMINAL = {"COMPLETED", "FAILED", "BLOCKED", "CANCELLED"}
EVENT_STATE = {
    "TurnStarted": "RUNNING", "FilterCompleted": "FILTERING",
    "ModelSelected": "ROUTING", "ContextBuildCompleted": "RETRIEVING",
    "ContextResolved": "RETRIEVING", "BudgetApplied": "READY",
    "ThinkingStarted": "EXECUTING", "ToolCallProposed": "EXECUTING",
    "ToolStarted": "EXECUTING", "ToolCompleted": "EXECUTING",
    "ApprovalRequired": "WAITING_APPROVAL", "EditApplied": "VALIDATING",
    "MetricsRecorded": "VALIDATING", "TurnCompleted": "COMPLETED",
    "TurnFailed": "FAILED", "TurnBlocked": "BLOCKED", "TurnCancelled": "CANCELLED",
}
MUTATING_LEGACY = {"write_file", "apply_patch", "run_command", "child_spawn", "goal_create", "goal_add_gate", "harness_remember", "queue_input"}
FILE_MUTATIONS = {"write_file", "apply_patch"}
TOOL_LEASE_HEARTBEAT_SECONDS = 60.0


def _repo(processor):
    return getattr(getattr(processor, "history_service", None), "repo", None)


def _db(processor):
    return getattr(_repo(processor), "db", None)


def _state_store(processor, conversation_id: str):
    db = _db(processor)
    if db is None or not conversation_id:
        return None
    try:
        return RuntimeStateStore(db, processor.workspace_id, conversation_id)
    except Exception:
        return None


def _new_execution_budget(processor) -> ExecutionBudgetLedger:
    config = getattr(processor, "config", None) or RuntimeConfig()
    max_input = max(1, int(getattr(config, "max_input_tokens_per_turn", 262144)))
    max_output = max(1, int(getattr(config, "max_output_tokens_per_turn", 131072)))
    max_total = max(
        max_input,
        max_output,
        int(getattr(config, "max_total_tokens_per_turn", 327680)),
    )
    return ExecutionBudgetLedger(
        ExecutionBudget(
            max_model_calls=max(
                1, int(getattr(config, "max_model_calls_per_turn", 24))
            ),
            max_input_tokens=max_input,
            max_output_tokens=max_output,
            max_total_tokens=max_total,
            max_cost=max(0.0, float(getattr(config, "max_cost_per_turn", 50.0))),
            max_duration_ms=max(
                1000,
                int(
                    float(
                        getattr(config, "max_turn_duration_seconds", 1800.0)
                    )
                    * 1000
                ),
            ),
            max_tool_calls=max(
                1, int(getattr(config, "max_tool_calls_per_turn", 8))
            ),
            max_subagents=max(
                0, int(getattr(config, "max_subagents_per_turn", 4))
            ),
        )
    )


def reserve_child_budget(
    processor,
    parent_turn_id: str,
    child_id: str,
    token_cap: int,
):
    budgets = getattr(processor, "execution_budgets", None)
    if budgets is None:
        budgets = {}
        processor.execution_budgets = budgets
    ledger = budgets.get(parent_turn_id)
    if ledger is None:
        ledger = _new_execution_budget(processor)
        budgets[parent_turn_id] = ledger

    requested_tokens = max(1, int(token_cap))
    call_cap = max(
        1,
        min(
            8,
            int(getattr(processor.config, "max_model_calls_per_turn", 24)),
        ),
    )
    snapshot = ledger.snapshot()
    budget_data = snapshot["budget"]
    usage = snapshot["usage"]
    reserved = snapshot["reserved"]
    max_total_tokens = max(1, int(budget_data["max_total_tokens"]))
    max_cost = max(0.0, float(budget_data["max_cost"]))
    available_cost = max(
        0.0,
        max_cost
        - float(usage.get("cost", 0.0) or 0.0)
        - float(reserved.get("cost", 0.0) or 0.0),
    )
    proportional_cost = max_cost * min(
        1.0,
        requested_tokens / max_total_tokens,
    )
    cost_cap = min(available_cost, proportional_cost)

    max_tools = max(0, int(budget_data["max_tool_calls"]))
    available_tools = max(
        0,
        max_tools
        - int(usage.get("tool_calls", 0) or 0)
        - int(reserved.get("tools", 0) or 0),
    )
    tool_cap = min(4, available_tools)
    if max_tools > 0 and tool_cap <= 0:
        raise ExecutionBudgetExceeded("no tool capacity remains for subagent")

    max_duration_ms = max(0, int(budget_data["max_duration_ms"]))
    remaining_duration_ms = max(
        0,
        max_duration_ms - int(usage.get("duration_ms", 0) or 0),
    )
    if max_duration_ms > 0 and remaining_duration_ms <= 0:
        raise ExecutionBudgetExceeded("no duration capacity remains for subagent")

    lease = ledger.reserve_subagent(
        child_id,
        token_cap=requested_tokens,
        call_cap=call_cap,
        cost_cap=cost_cap,
        tool_cap=tool_cap,
    )
    lease.reserved["duration_ms"] = remaining_duration_ms
    leases = getattr(processor, "child_budget_leases", None)
    if leases is None:
        leases = {}
        processor.child_budget_leases = leases
    leases[child_id] = {
        "turn_id": parent_turn_id,
        "lease_id": lease.id,
        "token_cap": lease.token_cap,
        "call_cap": lease.call_cap,
        "cost_cap": lease.cost_cap,
        "tool_cap": int(lease.reserved.get("tools", 0) or 0),
    }
    return lease


def settle_child_budget(
    processor,
    child_id: str,
    tokens_used: int = 0,
    calls_used: int = 0,
    cost_used: float = 0.0,
    tools_used: int = 0,
) -> None:
    leases = getattr(processor, "child_budget_leases", {})
    binding = leases.get(child_id)
    if not binding:
        return
    ledger = getattr(processor, "execution_budgets", {}).get(
        binding["turn_id"]
    )
    if ledger is None:
        return

    token_cap = max(0, int(binding.get("token_cap", 0)))
    call_cap = max(0, int(binding.get("call_cap", 0)))
    cost_cap = max(0.0, float(binding.get("cost_cap", 0.0)))
    reported_tokens = max(0, int(tokens_used))
    reported_calls = max(0, int(calls_used))
    reported_cost = max(0.0, float(cost_used))
    reported_tools = max(0, int(tools_used))
    tool_cap = max(0, int(binding.get("tool_cap", 0)))

    # A corrupt/overspent child report must never release capacity as if the
    # excess did not happen. Charge the full lease in that case; otherwise
    # charge the exact reported usage.
    charge_tokens = (
        token_cap if reported_tokens > token_cap else reported_tokens
    )
    charge_calls = call_cap if reported_calls > call_cap else reported_calls
    charge_cost = cost_cap if reported_cost > cost_cap else reported_cost
    charge_tools = tool_cap if reported_tools > tool_cap else reported_tools

    ledger.consume_child(
        binding["lease_id"],
        tokens=charge_tokens,
        calls=charge_calls,
        cost=charge_cost,
        tools=charge_tools,
    )
    ledger.settle_child(binding["lease_id"])
    leases.pop(child_id, None)


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except Exception:
        if isinstance(value, dict):
            return {str(k): _jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_jsonable(v) for v in value]
        return str(value)


def _fingerprint(name: str, args: dict[str, Any]) -> str:
    raw = json.dumps([name, _jsonable(args)], sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _ensure_turn(processor, cmd: TurnCommand) -> bool:
    """Ensure a durable row only when its parent conversation is persistent.

    Tests, ephemeral/headless callers and --no-history paths may intentionally
    execute a TurnCommand whose conversation does not exist in SQLite. Durable
    execution must remain an additive capability, never turn persistence into a
    new runtime precondition.
    """
    db = _db(processor)
    if db is None:
        return False
    try:
        with db.get_connection() as conn:
            if conn.execute("SELECT 1 FROM turns WHERE id=?", (cmd.turn_id,)).fetchone():
                return True
            if not conn.execute("SELECT 1 FROM conversations WHERE id=?", (cmd.conversation_id,)).fetchone():
                return False
            ordinal = conn.execute(
                "SELECT COALESCE(MAX(ordinal),0)+1 FROM turns WHERE conversation_id=?",
                (cmd.conversation_id,),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO turns (id,conversation_id,ordinal,state,mode,started_at) VALUES (?,?,?,?,?,?)",
                (cmd.turn_id, cmd.conversation_id, ordinal, "CREATED", cmd.mode, time.time()),
            )
            return True
    except Exception:
        return False


def _turn(processor, turn_id: str) -> dict[str, Any] | None:
    db = _db(processor)
    if db is None:
        return None
    try:
        with db.get_connection() as conn:
            row = conn.execute("SELECT * FROM turns WHERE id=?", (turn_id,)).fetchone()
            return dict(row) if row else None
    except Exception:
        return None


def _message(processor, turn_id: str, role: str) -> str:
    db = _db(processor)
    if db is None:
        return ""
    try:
        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT content FROM messages WHERE turn_id=? AND role=? ORDER BY created_at DESC LIMIT 1",
                (turn_id, role),
            ).fetchone()
            return str(row[0]) if row else ""
    except Exception:
        return ""


def _set_turn_state(processor, cmd: TurnCommand, state: str, error: str | None = None) -> None:
    db = _db(processor)
    if db is None:
        return
    task = getattr(getattr(processor, "session_state", None), "last_task", None)
    completed = time.time() if state in TERMINAL else None
    try:
        with db.get_connection() as conn:
            conn.execute(
                """UPDATE turns SET state=?, mode=?, semantic_intent=COALESCE(?,semantic_intent),
                risk=COALESCE(?,risk), confidence=COALESCE(?,confidence),
                completed_at=COALESCE(?,completed_at), error_code=?
                WHERE id=? AND conversation_id=?""",
                (state, cmd.mode, getattr(task, "intent", None), getattr(task, "risk", None),
                 getattr(task, "confidence", None), completed, str(error)[:512] if error else None,
                 cmd.turn_id, cmd.conversation_id),
            )
    except Exception:
        return


def _record_route(processor, cmd: TurnCommand, profile: str, success: bool, duration_ms: float) -> None:
    db = _db(processor)
    if db is None or not profile:
        return
    route = f"routing:{profile}:{'success' if success else 'failure'}"
    event_id = hashlib.sha256(f"{cmd.turn_id}:{route}".encode()).hexdigest()[:24]
    try:
        with db.get_connection() as conn:
            if not conn.execute("SELECT 1 FROM turns WHERE id=?", (cmd.turn_id,)).fetchone():
                return
            conn.execute(
                "INSERT OR REPLACE INTO telemetry_events (id,conversation_id,turn_id,route,start_time,duration_ms,input_tokens,output_tokens,tokens_saved) VALUES (?,?,?,?,?,?,?,?,?)",
                (event_id, cmd.conversation_id, cmd.turn_id, route, time.time(), duration_ms, 0, 0, 0),
            )
    except Exception:
        return


def routing_feedback_snapshot(processor) -> dict[str, dict[str, float | int]]:
    """Load routing quality for all profiles with a single SQLite query."""
    db = _db(processor)
    if db is None:
        return {}
    try:
        with db.get_connection() as conn:
            rows = conn.execute(
                """SELECT route, COUNT(*), COALESCE(AVG(duration_ms), 0)
                FROM telemetry_events
                WHERE route LIKE 'routing:%'
                GROUP BY route"""
            ).fetchall()
    except Exception:
        return {}

    totals: dict[str, dict[str, float | int]] = {}
    for route, count, avg_duration in rows:
        parts = str(route or "").split(":")
        if len(parts) != 3 or parts[0] != "routing":
            continue
        profile, outcome = parts[1], parts[2]
        if outcome not in {"success", "failure"}:
            continue
        bucket = totals.setdefault(
            profile,
            {"samples": 0, "successes": 0, "duration_weighted": 0.0},
        )
        sample_count = int(count or 0)
        bucket["samples"] = int(bucket["samples"]) + sample_count
        if outcome == "success":
            bucket["successes"] = int(bucket["successes"]) + sample_count
        bucket["duration_weighted"] = float(bucket["duration_weighted"]) + (
            float(avg_duration or 0.0) * sample_count
        )

    result: dict[str, dict[str, float | int]] = {}
    for profile, bucket in totals.items():
        samples = int(bucket["samples"])
        successes = int(bucket["successes"])
        result[profile] = {
            "samples": samples,
            "success_rate": (successes / samples) if samples else 0.5,
            "avg_duration_ms": (
                float(bucket["duration_weighted"]) / samples if samples else 0.0
            ),
        }
    return result


def _expire_code_memory(processor, paths: list[str]) -> None:
    """Invalidate only unpinned code-derived facts with direct path evidence."""
    db = _db(processor)
    if db is None:
        return
    now = time.time()
    try:
        with db.get_connection() as conn:
            for path in paths[:64]:
                conn.execute(
                    """UPDATE memories SET status='SUPERSEDED', valid_until=?, updated_at=?
                    WHERE workspace_id=? AND status='ACTIVE' AND pinned=0
                    AND kind IN ('TECHNICAL_FACT','PROJECT_STATE','OPEN_ISSUE')
                    AND id IN (SELECT memory_id FROM memory_evidence
                               WHERE workspace_id=? AND evidence_text LIKE ?)""",
                    (now, now, processor.workspace_id, processor.workspace_id, f"%{path}%"),
                )
    except Exception:
        return


def adaptive_retrieval_ratio(processor, task: Any, cmd: TurnCommand) -> float:
    ratio = float(getattr(processor.config, "context_retrieval_token_ratio", 0.25))
    risk = str(getattr(task, "risk", "LOW")).upper()
    intent = str(getattr(task, "intent", "ASK")).upper()
    confidence = float(getattr(task, "confidence", 1.0) or 0.0)
    if risk in {"HIGH", "CRITICAL"}:
        ratio += 0.10
    if intent in {"DEBUG", "REVIEW", "REFACTOR"}:
        ratio += 0.08
    elif intent in {"IMPLEMENT", "TEST"}:
        ratio += 0.04
    if confidence < 0.70:
        ratio += 0.08
    elif confidence >= 0.90 and (getattr(task, "paths", None) or getattr(task, "symbols", None)):
        ratio -= 0.05
    if cmd.explicit_files:
        ratio -= 0.03
    return max(0.10, min(ratio, 0.50))


def _is_mutating(name: str, args: dict[str, Any]) -> bool:
    if name in MUTATING_LEGACY:
        return True
    if name != "kitt_runtime":
        return False
    op = str(args.get("operation") or "")
    try:
        from kitt.runtime.safe_runtime import OPERATION_SPECS
        spec = OPERATION_SPECS.get(op)
        return bool(spec and spec.sensitive)
    except Exception:
        return op in {"repo.edit_symbol", "patch.apply", "process.run", "state.set"}


def _file_mutation(name: str, args: dict[str, Any]) -> bool:
    return name in FILE_MUTATIONS or (
        name == "kitt_runtime" and str(args.get("operation") or "") in {"repo.edit_symbol", "patch.apply"}
    )


def _affected_paths(processor, name: str, args: dict[str, Any], result: Any) -> list[str]:
    try:
        found = processor._paths_from_tool(name, args, result)
        if found:
            return list(dict.fromkeys(str(p) for p in found if p))
    except Exception:
        pass
    inner = args.get("arguments", {}) if name == "kitt_runtime" else args
    if isinstance(inner, dict):
        path = inner.get("path") or inner.get("file")
        if path:
            return [str(path)]
        if isinstance(inner.get("paths"), list):
            return [str(p) for p in inner["paths"][:64] if p]
    return []


def _durable_event_payload(event: Any) -> dict[str, Any]:
    if is_dataclass(event):
        data = {
            item.name: _jsonable(getattr(event, item.name))
            for item in fields(event)
            if item.name != "timestamp"
        }
    elif isinstance(event, dict):
        data = {str(key): _jsonable(value) for key, value in event.items()}
    else:
        data = {"value": _jsonable(event)}

    # Large user/model/tool payloads have their own durable owners. Keep event
    # ordering and evidence without duplicating arbitrarily large text here.
    for key in ("output", "response", "prompt"):
        value = data.get(key)
        if isinstance(value, str) and len(value) > 4096:
            encoded = value.encode("utf-8")
            data[key] = {
                "sha256": hashlib.sha256(encoded).hexdigest(),
                "chars": len(value),
                "preview": value[:1024],
            }
    return data


class DurableTurnJournal:
    """Project processor events onto canonical durable state and evidence."""

    def __init__(self, processor):
        self.processor = processor
        self.profiles: dict[str, str] = {}
        self.started: dict[str, float] = {}
        self.episode_ids: dict[str, str] = {}
        self.memory_sessions: set[str] = set()
        self.db = _db(processor)
        self.ledger = None
        self.episodes = None
        self.invariants = None
        self.run_coordinator = getattr(processor, "run_coordinator", None)
        if self.db is not None:
            projections = build_default_projection_registry(self.db)
            self.ledger = getattr(processor, "event_ledger", None)
            if self.ledger is None:
                self.ledger = EventLedger(self.db, projections)
                processor.event_ledger = self.ledger
            self.episodes = TaskEpisodeService(
                self.db,
                self.ledger,
                getattr(getattr(processor, "registry", None), "goal_service", None),
            )
            self.invariants = RuntimeInvariantService(self.db)

    def turn_record(self, turn_id: str) -> dict[str, Any] | None:
        """Return the persisted turn row used by native TurnProcessor lifecycle hooks."""
        return _turn(self.processor, turn_id)

    def _episode_for_turn(self, turn_id: str) -> str | None:
        cached = self.episode_ids.get(turn_id)
        if cached:
            return cached
        if self.episodes is None:
            return None
        try:
            episode = self.episodes.for_turn(turn_id)
        except Exception:
            return None
        if episode is not None:
            self.episode_ids[turn_id] = episode.id
            return episode.id
        return None

    def _memory_lifecycle(
        self,
        event: str,
        *,
        source_id: str,
        source_revision: str,
        evidence: dict[str, Any],
        source_kind: str = "agent",
    ) -> None:
        memory = getattr(self.processor, "memory", None)
        hook = getattr(memory, "lifecycle_event", None)
        if not callable(hook):
            return
        try:
            hook(
                event,
                source_id=source_id,
                source_revision=source_revision,
                evidence=evidence,
                source_kind=source_kind,
            )
        except Exception:
            # Lifecycle evidence is fail-open and never owns turn execution.
            pass

    def begin(self, cmd: TurnCommand) -> None:
        persistent = _ensure_turn(self.processor, cmd)
        self.started[cmd.turn_id] = time.time()
        if cmd.conversation_id not in self.memory_sessions:
            self._memory_lifecycle(
                "session.started",
                source_id=cmd.conversation_id,
                source_revision="1",
                evidence={
                    "conversation_id": cmd.conversation_id,
                    "workspace_id": self.processor.workspace_id,
                },
                source_kind="session",
            )
            self.memory_sessions.add(cmd.conversation_id)
        self._memory_lifecycle(
            "turn.started",
            source_id=cmd.turn_id,
            source_revision=cmd.turn_id,
            evidence={
                "conversation_id": cmd.conversation_id,
                "turn_id": cmd.turn_id,
                "mode": cmd.mode,
            },
            source_kind="agent",
        )
        budgets = getattr(self.processor, "execution_budgets", None)
        if budgets is None:
            budgets = {}
            self.processor.execution_budgets = budgets
        if cmd.turn_id not in budgets:
            budgets[cmd.turn_id] = _new_execution_budget(self.processor)
        if persistent and self.run_coordinator is not None:
            self.run_coordinator.transition(
                cmd.conversation_id,
                cmd.turn_id,
                "RUNNING",
                reason="turn-begin",
            )
        if persistent:
            store = _state_store(self.processor, cmd.conversation_id)
            if store:
                try:
                    store.set(f"turn:{cmd.turn_id}:checkpoint", {
                        "turn_id": cmd.turn_id, "conversation_id": cmd.conversation_id,
                        "prompt": cmd.prompt, "mode": cmd.mode,
                        "explicit_files": sorted(cmd.explicit_files), "dry_run": bool(cmd.dry_run),
                        "state": "RUNNING", "updated_at": time.time()}, ttl_seconds=604800)
                except Exception:
                    pass
            if self.episodes is not None:
                try:
                    episode = self.episodes.begin_turn(
                        cmd.conversation_id,
                        cmd.turn_id,
                        cmd.prompt,
                    )
                    self.episode_ids[cmd.turn_id] = episode.id
                except Exception:
                    # Evidence is fail-open. Canonical turn execution remains the
                    # authority when optional evidence persistence is unavailable.
                    pass
        self.state(cmd, "RUNNING")

    def state(self, cmd: TurnCommand, state: str, error: str | None = None) -> None:
        _set_turn_state(self.processor, cmd, state, error)
        if _turn(self.processor, cmd.turn_id):
            store = _state_store(self.processor, cmd.conversation_id)
            if store:
                try:
                    key = f"turn:{cmd.turn_id}:checkpoint"
                    value = store.get(key) or {}
                    if isinstance(value, dict):
                        value.update({"state": state, "updated_at": time.time()})
                        if error:
                            value["error"] = str(error)[:2000]
                        store.set(key, value, ttl_seconds=604800)
                except Exception:
                    pass
        try:
            self.processor._emit("TurnStateChanged", {"turn_id": cmd.turn_id,
                "conversation_id": cmd.conversation_id, "state": state})
        except Exception:
            pass

    def end_sessions(self) -> None:
        for conversation_id in list(self.memory_sessions):
            self._memory_lifecycle(
                "session.ended",
                source_id=conversation_id,
                source_revision="closed",
                evidence={
                    "conversation_id": conversation_id,
                    "workspace_id": self.processor.workspace_id,
                },
                source_kind="session",
            )
        self.memory_sessions.clear()

    def record_model_request(
        self,
        *,
        conversation_id: str,
        turn_id: str,
        system_prompt: str,
        messages: list[dict[str, Any]],
        route: str = "",
        profile: str = "",
        model: str = "",
    ) -> None:
        if self.ledger is None or not conversation_id or not _turn(self.processor, turn_id):
            return
        try:
            self.ledger.append_model_request(
                conversation_id,
                turn_id,
                system_prompt=system_prompt,
                messages=messages,
                route=route,
                profile=profile,
                model=model,
                episode_id=self._episode_for_turn(turn_id),
            )
        except Exception:
            pass

    def record_deliverables(
        self,
        turn_id: str,
        paths: list[str],
        *,
        kind: str,
    ) -> None:
        if self.episodes is None or not paths:
            return
        episode_id = self._episode_for_turn(turn_id)
        if not episode_id:
            return
        try:
            self.episodes.record_deliverables(
                episode_id,
                turn_id,
                paths,
                kind=kind,
            )
        except Exception:
            pass

    def record_learning_outcome(
        self,
        cmd: TurnCommand,
        *,
        episode_id: str | None,
        state: str,
    ) -> None:
        if not episode_id or state not in {"COMPLETED", "FAILED"}:
            return
        harness = getattr(self.processor, "harness_service", None)
        if harness is None or self.ledger is None:
            return
        try:
            efficiency = harness.episode_efficiency(episode_id)
        except Exception:
            efficiency = None
        try:
            candidates = harness.learning_capture(
                self.processor.workspace_id,
                min_occurrences=2,
                limit=5,
            )
        except Exception:
            candidates = []

        payload = {
            "episode_id": episode_id,
            "terminal_state": state,
            "efficiency": efficiency,
            "learning_candidates": [
                {
                    "normalized_objective": item.get(
                        "normalized_objective",
                        "",
                    ),
                    "count": int(item.get("count", 0) or 0),
                    "recommended_owner": item.get(
                        "recommended_owner",
                        "",
                    ),
                    "requires_intervention": bool(
                        item.get("requires_intervention", True)
                    ),
                    "episode_ids": list(
                        item.get("episode_ids") or ()
                    )[:20],
                }
                for item in candidates
            ],
            "auto_apply": False,
        }
        self.ledger.append_event(
            cmd.conversation_id,
            "EpisodeLearningObserved",
            payload,
            turn_id=cmd.turn_id,
            episode_id=episode_id,
            source="learning-loop",
            durability="DURABLE",
            model_visible=False,
            replayable=True,
        )

    def observe(self, cmd: TurnCommand, event: Any) -> None:
        name = type(event).__name__
        budget = getattr(self.processor, "execution_budgets", {}).get(cmd.turn_id)
        if budget is not None and name == "ApprovalRequired":
            budget.pause()
        state = EVENT_STATE.get(name)
        event_record = None
        episode_id = self._episode_for_turn(cmd.turn_id)
        if self.ledger is not None and _turn(self.processor, cmd.turn_id):
            try:
                event_record = self.ledger.append(
                    cmd.conversation_id,
                    name,
                    _durable_event_payload(event),
                    turn_id=cmd.turn_id,
                    episode_id=episode_id,
                    force_checkpoint=state in TERMINAL,
                    source="turn-processor",
                    durability="DURABLE",
                )
            except Exception:
                event_record = None
        if event_record is not None and self.episodes is not None and episode_id:
            try:
                self.episodes.observe(
                    episode_id,
                    name,
                    event_record.payload,
                    event_ref=f"event:{event_record.id}",
                )
            except Exception:
                pass

        if name == "ToolCompleted":
            payload = _durable_event_payload(event)
            self._memory_lifecycle(
                "tool.completed",
                source_id=(
                    f"{cmd.turn_id}:"
                    f"{str(payload.get('call_id') or payload.get('tool_name') or 'tool')}"
                ),
                source_revision=str(payload.get("success")),
                evidence={
                    "tool": str(payload.get("tool_name") or ""),
                    "success": bool(payload.get("success")),
                    "tokens": int(payload.get("tokens") or 0),
                },
                source_kind="tool",
            )

        if (
            event_record is not None
            and self.run_coordinator is not None
            and name in {
                "TurnStarted",
                "ApprovalRequired",
                "TurnCompleted",
                "TurnFailed",
                "TurnBlocked",
                "TurnCancelled",
            }
        ):
            self.run_coordinator.observe_event(
                cmd.conversation_id,
                cmd.turn_id,
                name,
            )

        if name == "ModelSelected":
            self.profiles[cmd.turn_id] = str(getattr(event, "profile_name", "") or "")
        if state:
            self.state(cmd, state, getattr(event, "error", None) if state == "FAILED" else None)
        if state in TERMINAL:
            self._memory_lifecycle(
                "turn.completed",
                source_id=cmd.turn_id,
                source_revision=state,
                evidence={
                    "conversation_id": cmd.conversation_id,
                    "turn_id": cmd.turn_id,
                    "terminal_state": state,
                    "event": name,
                },
                source_kind="agent",
            )
            budget = getattr(self.processor, "execution_budgets", {}).get(cmd.turn_id)
            if budget is not None:
                snapshots = getattr(
                    self.processor,
                    "execution_budget_snapshots",
                    None,
                )
                if snapshots is None:
                    snapshots = {}
                    self.processor.execution_budget_snapshots = snapshots
                snapshots[cmd.turn_id] = budget.snapshot()
            profile = self.profiles.pop(cmd.turn_id, "")
            started = self.started.pop(cmd.turn_id, time.time())
            if state in {"COMPLETED", "FAILED"}:
                _record_route(
                    self.processor,
                    cmd,
                    profile,
                    state == "COMPLETED",
                    max(0.0, (time.time() - started) * 1000.0),
                )
            if self.episodes is not None:
                try:
                    self.episodes.settle_for_turn(
                        cmd.turn_id,
                        state,
                        outcome={"event": name},
                    )
                except Exception:
                    pass
            try:
                self.record_learning_outcome(
                    cmd,
                    episode_id=episode_id,
                    state=state,
                )
            except Exception:
                # Learning is evidence-only and must never change turn outcome.
                pass
            if self.invariants is not None:
                try:
                    self.invariants.check_terminal(cmd.conversation_id, cmd.turn_id)
                except Exception:
                    if self.invariants.mode == "STRICT":
                        raise



def execute_tool_with_engineering(
    processor,
    registry,
    base_execute,
    name,
    args,
    *pos,
    **kwargs,
):
    """Apply Agent-owned execution controls around the registry's canonical executor."""
    arguments = args if isinstance(args, dict) else {}
    conv = str(kwargs.get("conversation_id") or "")
    turn = str(kwargs.get("turn_id") or "")
    budget = getattr(processor, "execution_budgets", {}).get(turn)
    if budget is not None:
        operation = (
            str(arguments.get("operation") or "")
            if name == "kitt_runtime" and isinstance(arguments, dict)
            else ""
        )
        stage = "validator" if operation == "plan.verify" else "tools"
        budget.reserve_tool_call(stage=stage)

    role_policy = getattr(processor, "_agent_role_policies", {}).get(turn)
    if role_policy is not None and not role_policy.allows_tool(name, arguments):
        from kitt.tools.registry_core import ToolResult

        return ToolResult(
            False,
            "",
            error=(
                f"Agent role {role_policy.role} does not allow "
                f"{name} for this turn"
            ),
            metadata={
                "agent_role": str(role_policy.role),
                "role_policy_denied": True,
            },
        )

    security_context = kwargs.get("security_context")
    coordinator = getattr(processor, "run_coordinator", None)
    resource_coordinator = getattr(processor, "resource_coordinator", None)
    claimed = False
    resource_claimed = False
    resource_owner = f"resource:{conv}:{turn}"
    if _is_mutating(name, arguments) and security_context is not None:
        sandbox = getattr(
            getattr(registry, "process_runner", None),
            "sandbox",
            None,
        )
        sandbox_profile = str(
            getattr(sandbox, "default_profile", "workspace-write")
        )
        authority = capture_authority_snapshot(
            security_context,
            policy=registry.policy,
            autonomy=registry.policy.autonomy,
            approval_manager=registry.approval_manager,
            sandbox_profile=sandbox_profile,
        )
        validate_authority_snapshot(
            authority,
            security_context,
            policy=registry.policy,
            autonomy=registry.policy.autonomy,
            approval_manager=registry.approval_manager,
            sandbox_profile=sandbox_profile,
        )
        if coordinator is not None and conv and turn:
            coordinator.claim_tool(
                conv,
                turn,
                name,
                arguments,
            )
            claimed = True
        if resource_coordinator is not None and conv and turn:
            resources = resource_coordinator.resources_for_tool(
                name,
                arguments,
                conversation_id=conv,
            )
            if resources:
                resource_coordinator.acquire_many(
                    resources,
                    resource_owner,
                    intent=f"{name} execution",
                )
                resource_claimed = True

    lease_stop = threading.Event()
    lease_thread = None
    if claimed or resource_claimed:
        turn_owner = f"turn:{conv}:{turn}"

        def refresh_leases() -> None:
            while not lease_stop.wait(TOOL_LEASE_HEARTBEAT_SECONDS):
                try:
                    workspace = getattr(coordinator, "workspace", None)
                    if claimed and workspace is not None:
                        workspace.refresh_owner(turn_owner, ttl_seconds=180.0)
                    if resource_claimed and resource_coordinator is not None:
                        resource_coordinator.refresh_owner(
                            resource_owner,
                            ttl_seconds=180.0,
                        )
                except Exception:
                    # Coordination remains fail-closed on acquisition. Renewal
                    # is best-effort so a transient DB error does not abort a
                    # side effect that may already be running.
                    continue

        lease_thread = threading.Thread(
            target=refresh_leases,
            name=f"kitt-tool-lease-{turn[:12]}",
            daemon=True,
        )
        lease_thread.start()

    snapshot_service = getattr(
        processor,
        "workspace_snapshot_service",
        None,
    )
    snapshot = None
    try:
        if (
            snapshot_service is not None
            and coordinator is not None
            and conv
            and turn
            and _file_mutation(name, arguments)
        ):
            planned_paths = coordinator.mutation_paths(name, arguments)
            if (
                planned_paths
                and "." not in planned_paths
                and all(path for path in planned_paths)
            ):
                snapshot = snapshot_service.capture(
                    conversation_id=conv,
                    turn_id=turn,
                    paths=planned_paths,
                )

        plans = getattr(processor, "task_plans", None)
        check_binding = (
            plans.registered_check(conv, turn, name, arguments)
            if plans and conv and turn
            else []
        )
        result = base_execute(name, args, *pos, **kwargs)
        if plans and check_binding:
            plans.record_check(conv, turn, check_binding, result)
        paths = _affected_paths(processor, name, arguments, result)
        if snapshot is not None:
            result.metadata = dict(getattr(result, "metadata", {}) or {})
            result.metadata["workspace_snapshot_id"] = snapshot.snapshot_id

        if (
            getattr(result, "success", False)
            and _file_mutation(name, arguments)
            and paths
        ):
            rollback_guard = None
            if snapshot is not None and snapshot_service is not None:
                rollback_guard = {
                    item["path"]: item["current_sha256"]
                    for item in snapshot_service.diff(
                        snapshot.snapshot_id,
                        conversation_id=conv,
                        turn_id=turn,
                    )
                }
            tokens = getattr(processor, "cancellation_registry", None)
            verifier = getattr(registry, "_agent_verifier", None)
            if verifier is None:
                verifier = VerificationOrchestrator(
                    registry.root_path,
                    registry.process_runner,
                )
                registry._agent_verifier = verifier
            report = verifier.verify(
                paths,
                cancellation=(
                    tokens.token(turn)
                    if tokens is not None and turn
                    else None
                ),
            )
            result.metadata = dict(getattr(result, "metadata", {}) or {})
            result.metadata["verification"] = report.as_dict()
            if not report.ok:
                if snapshot is not None and snapshot_service is not None:
                    try:
                        restored = snapshot_service.restore(
                            snapshot.snapshot_id,
                            conversation_id=conv,
                            turn_id=turn,
                            expected_current=rollback_guard,
                        )
                        result.metadata["post_edit_rolled_back"] = True
                        result.metadata["rollback_paths"] = restored
                    except ValueError as exc:
                        result.metadata["post_edit_rollback_failed"] = True
                        result.metadata["rollback_conflict"] = str(exc)
                result.success = False
                result.error = (
                    "Post-edit verification failed:\n"
                    + report.failure_message()
                )
        if getattr(result, "success", False) and paths:
            _expire_code_memory(processor, paths)
            journal = getattr(processor, "turn_journal", None)
            if journal is not None:
                inner_operation = (
                    str(arguments.get("operation") or name)
                    if name == "kitt_runtime"
                    and isinstance(arguments, dict)
                    else name
                )
                journal.record_deliverables(
                    turn,
                    paths,
                    kind=inner_operation,
                )
        return result
    finally:
        lease_stop.set()
        if lease_thread is not None:
            lease_thread.join(timeout=0.2)
        if resource_claimed and resource_coordinator is not None:
            resource_coordinator.release_owner(resource_owner)
        if claimed and coordinator is not None:
            coordinator.release_tool(conv, turn)


def resume_turn(processor, turn_id: str, grant=None):
    """Resume a durable turn through TurnProcessor's native public entrypoints."""
    row = _turn(processor, turn_id)
    if not row:
        yield TurnFailed(error=f"Unknown or non-persistent turn: {turn_id}")
        return
    state = str(row.get("state") or "CREATED")
    conv = str(row.get("conversation_id") or "")
    if state == "COMPLETED":
        yield TurnCompleted(
            response=(
                _message(processor, turn_id, "assistant")
                or "[Turn already completed]"
            ),
            edit_result=None,
        )
        return
    if state == "WAITING_APPROVAL":
        repo = _repo(processor)
        pending = (
            repo.get_valid_pending_action(
                f"pa_{turn_id}",
                processor.workspace_id,
            )
            if repo is not None
            else None
        )
        if pending:
            if grant is not None:
                yield from processor.continue_turn(turn_id, grant)
                return
            yield ApprovalRequired(
                turn_id=turn_id,
                conversation_id=conv,
                tool_name=pending.tool_name,
                args=pending.normalized_args,
                action_hash=pending.action_hash,
                approval_request_id=pending.approval_request_id,
                workspace_id=pending.workspace_id,
            )
            return
    store = _state_store(processor, conv)
    checkpoint = store.get(f"turn:{turn_id}:checkpoint") if store else None
    if not isinstance(checkpoint, dict):
        checkpoint = {
            "prompt": _message(processor, turn_id, "user"),
            "mode": row.get("mode", "auto"),
            "explicit_files": [],
        }
    prompt = str(checkpoint.get("prompt") or "")
    if not prompt:
        yield TurnFailed(error="Turn has no persisted resumable prompt.")
        return
    yield from processor.run_turn(
        TurnCommand(
            conversation_id=conv,
            prompt=prompt,
            mode=str(checkpoint.get("mode") or row.get("mode") or "auto"),
            explicit_files=set(checkpoint.get("explicit_files") or []),
            dry_run=bool(checkpoint.get("dry_run", False)),
            turn_id=turn_id,
        )
    )


def install_agent_engineering(processor, registry) -> None:
    """Install durable services and callbacks without replacing public entrypoints."""
    if getattr(processor, "_agent_engineering_installed", False):
        return
    processor._agent_engineering_installed = True
    journal = DurableTurnJournal(processor)
    processor.turn_journal = journal
    processor._record_model_request = journal.record_model_request
    processor.session_ledger = journal.ledger
    from kitt.core.task_plan import TaskPlanCoordinator

    processor.task_plans = TaskPlanCoordinator(
        journal.ledger,
        registry.root_path,
        getattr(registry, "child_manager", None),
        max_iterations=min(
            3,
            int(getattr(processor.config, "max_correction_cycles", 2)) + 1,
        ),
    )
    registry.task_plans = processor.task_plans
    processor._logical_request_ids = {}
    registry.logical_request_ids = processor._logical_request_ids
    from kitt.core.cancellation import CancellationRegistry

    processor.cancellation_registry = CancellationRegistry()
    registry.cancellation_registry = processor.cancellation_registry
    processor.session_projections = (
        journal.ledger.projections if journal.ledger else None
    )
    processor.task_episodes = journal.episodes
    processor.runtime_invariants = journal.invariants
    registry._agent_verifier = VerificationOrchestrator(
        registry.root_path,
        registry.process_runner,
    )

    processor._agent_trace_context = None
    processor._adaptive_retrieval_ratio_fn = adaptive_retrieval_ratio
    processor._routing_feedback_snapshot_fn = routing_feedback_snapshot
