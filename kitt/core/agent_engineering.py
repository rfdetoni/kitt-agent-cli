"""KITT-native reliability, context, routing and verification controls.

The module composes services already owned by KITT. It adds no framework,
store or scheduler; SQLite turns, runtime_states and telemetry_events remain
canonical.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import replace
from types import MethodType
from typing import Any, Iterator

from kitt.core.turn_command import TurnCommand
from kitt.core.turn_events import ApprovalRequired, TurnCompleted, TurnFailed
from kitt.runtime.state import RuntimeStateStore
from kitt.validation.orchestrator import VerificationOrchestrator

_TERMINAL = {"COMPLETED", "FAILED", "BLOCKED", "CANCELLED"}
_EVENT_STATE = {
    "TurnStarted": "RUNNING", "FilterCompleted": "FILTERING",
    "ModelSelected": "ROUTING", "ContextBuildCompleted": "RETRIEVING",
    "ContextResolved": "RETRIEVING", "BudgetApplied": "READY",
    "ThinkingStarted": "EXECUTING", "ToolCallProposed": "EXECUTING",
    "ToolStarted": "EXECUTING", "ToolCompleted": "EXECUTING",
    "ApprovalRequired": "WAITING_APPROVAL", "EditApplied": "VALIDATING",
    "MetricsRecorded": "VALIDATING", "TurnCompleted": "COMPLETED",
    "TurnFailed": "FAILED", "TurnBlocked": "BLOCKED", "TurnCancelled": "CANCELLED",
}
_MUTATING_LEGACY = {"write_file", "apply_patch", "run_command", "child_spawn", "goal_create", "goal_add_gate", "harness_remember", "queue_input"}
_FILE_MUTATIONS = {"write_file", "apply_patch"}


def _repo(processor):
    return getattr(getattr(processor, "history_service", None), "repo", None)


def _db(processor):
    return getattr(_repo(processor), "db", None)


def _store(processor, conversation_id: str):
    db = _db(processor)
    if db is None or not conversation_id:
        return None
    try:
        return RuntimeStateStore(db, processor.workspace_id, conversation_id)
    except Exception:
        return None


def _safe_json(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except Exception:
        if isinstance(value, dict):
            return {str(k): _safe_json(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_safe_json(v) for v in value]
        return str(value)


def _fingerprint(tool_name: str, args: dict[str, Any]) -> str:
    raw = json.dumps([tool_name, _safe_json(args)], sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _ensure_turn(processor, cmd: TurnCommand) -> None:
    db = _db(processor)
    if db is None:
        return
    with db.get_connection() as conn:
        if conn.execute("SELECT 1 FROM turns WHERE id=?", (cmd.turn_id,)).fetchone():
            return
        ordinal = conn.execute("SELECT COALESCE(MAX(ordinal),0)+1 FROM turns WHERE conversation_id=?", (cmd.conversation_id,)).fetchone()[0]
        conn.execute(
            "INSERT INTO turns (id,conversation_id,ordinal,state,mode,started_at) VALUES (?,?,?,?,?,?)",
            (cmd.turn_id, cmd.conversation_id, ordinal, "CREATED", cmd.mode, time.time()),
        )


def _get_turn(processor, turn_id: str) -> dict[str, Any] | None:
    db = _db(processor)
    if db is None:
        return None
    with db.get_connection() as conn:
        row = conn.execute("SELECT * FROM turns WHERE id=?", (turn_id,)).fetchone()
        return dict(row) if row else None


def _update_turn(processor, cmd: TurnCommand, state: str, error: str | None = None) -> None:
    db = _db(processor)
    if db is None:
        return
    task = getattr(getattr(processor, "session_state", None), "last_task", None)
    completed = time.time() if state in _TERMINAL else None
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


def _turn_text(processor, turn_id: str, role: str) -> str:
    db = _db(processor)
    if db is None:
        return ""
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT content FROM messages WHERE turn_id=? AND role=? ORDER BY created_at DESC LIMIT 1",
            (turn_id, role),
        ).fetchone()
        return str(row[0]) if row else ""


def _save_routing(processor, cmd: TurnCommand, profile: str, success: bool, duration_ms: float) -> None:
    db = _db(processor)
    if db is None or not profile:
        return
    route = f"routing:{profile}:{'success' if success else 'failure'}"
    event_id = hashlib.sha256(f"{cmd.turn_id}:{route}".encode()).hexdigest()[:24]
    with db.get_connection() as conn:
        if not conn.execute("SELECT 1 FROM turns WHERE id=?", (cmd.turn_id,)).fetchone():
            return
        conn.execute(
            "INSERT OR REPLACE INTO telemetry_events (id,conversation_id,turn_id,route,start_time,duration_ms,input_tokens,output_tokens,tokens_saved) VALUES (?,?,?,?,?,?,?,?,?)",
            (event_id, cmd.conversation_id, cmd.turn_id, route, time.time(), duration_ms, 0, 0, 0),
        )


def _routing_feedback(processor, profile: str) -> dict[str, Any]:
    db = _db(processor)
    if db is None:
        return {"samples": 0, "success_rate": 0.5}
    with db.get_connection() as conn:
        row = conn.execute(
            """SELECT COUNT(*) n,
            COALESCE(SUM(CASE WHEN route LIKE '%:success' THEN 1 ELSE 0 END),0) ok,
            COALESCE(AVG(duration_ms),0) avg_ms FROM telemetry_events WHERE route LIKE ?""",
            (f"routing:{profile}:%",),
        ).fetchone()
        n = int(row[0]) if row else 0
        return {"samples": n, "success_rate": float(row[1]) / n if n else 0.5,
                "avg_duration_ms": float(row[2]) if row else 0.0}


def _invalidate_memory(processor, paths: list[str]) -> None:
    """Expire only unpinned code-derived memories evidenced by changed paths."""
    db = _db(processor)
    if db is None or not paths:
        return
    now = time.time()
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


def _is_mutating(tool_name: str, args: dict[str, Any]) -> bool:
    if tool_name in _MUTATING_LEGACY:
        return True
    if tool_name != "kitt_runtime":
        return False
    op = str(args.get("operation") or "")
    try:
        from kitt.runtime.safe_runtime import OPERATION_SPECS
        spec = OPERATION_SPECS.get(op)
        return bool(spec and spec.sensitive)
    except Exception:
        return op in {"repo.edit_symbol", "patch.apply", "process.run", "state.set"}


def _is_file_mutation(tool_name: str, args: dict[str, Any]) -> bool:
    return tool_name in _FILE_MUTATIONS or (
        tool_name == "kitt_runtime" and str(args.get("operation") or "") in {"repo.edit_symbol", "patch.apply"}
    )


def _paths(processor, tool_name: str, args: dict[str, Any], result: Any) -> list[str]:
    try:
        found = processor._paths_from_tool(tool_name, args, result)
        if found:
            return list(dict.fromkeys(str(p) for p in found if p))
    except Exception:
        pass
    inner = args.get("arguments", {}) if tool_name == "kitt_runtime" else args
    if isinstance(inner, dict):
        path = inner.get("path") or inner.get("file")
        if path:
            return [str(path)]
        if isinstance(inner.get("paths"), list):
            return [str(p) for p in inner["paths"][:64] if p]
    return []


def _adaptive_ratio(processor, task: Any, cmd: TurnCommand) -> float:
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


class TurnJournal:
    """Project yielded turn events onto canonical durable state."""
    def __init__(self, processor):
        self.processor = processor
        self.profiles: dict[str, str] = {}
        self.started: dict[str, float] = {}

    def begin(self, cmd: TurnCommand) -> None:
        _ensure_turn(self.processor, cmd)
        self.started[cmd.turn_id] = time.time()
        store = _store(self.processor, cmd.conversation_id)
        if store:
            try:
                store.set(
                    f"turn:{cmd.turn_id}:checkpoint",
                    {"turn_id": cmd.turn_id, "conversation_id": cmd.conversation_id,
                     "prompt": cmd.prompt, "mode": cmd.mode,
                     "explicit_files": sorted(cmd.explicit_files), "dry_run": bool(cmd.dry_run),
                     "state": "RUNNING", "updated_at": time.time()},
                    ttl_seconds=604800,
                )
            except Exception:
                pass
        self.state(cmd, "RUNNING")

    def state(self, cmd: TurnCommand, state: str, error: str | None = None) -> None:
        try:
            _update_turn(self.processor, cmd, state, error)
        except Exception:
            pass
        store = _store(self.processor, cmd.conversation_id)
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

    def observe(self, cmd: TurnCommand, event: Any) -> None:
        name = type(event).__name__
        state = _EVENT_STATE.get(name)
        if name == "ModelSelected":
            self.profiles[cmd.turn_id] = str(getattr(event, "profile_name", "") or "")
        if state:
            self.state(cmd, state, getattr(event, "error", None) if state == "FAILED" else None)
        if state in _TERMINAL:
            profile = self.profiles.pop(cmd.turn_id, "")
            started = self.started.pop(cmd.turn_id, time.time())
            try:
                _save_routing(self.processor, cmd, profile, state == "COMPLETED",
                              max(0.0, (time.time() - started) * 1000.0))
            except Exception:
                pass


def _install_tool_wrapper(processor, registry) -> None:
    if getattr(registry, "_agent_engineering_execute_installed", False):
        return
    original = registry.execute_tool
    verifier = VerificationOrchestrator(registry.root_path, registry.process_runner)

    def execute_tool(tool_name, args, *pos, **kwargs):
        arguments = args if isinstance(args, dict) else {}
        conv = str(kwargs.get("conversation_id") or "")
        turn = str(kwargs.get("turn_id") or "")
        store = _store(processor, conv)
        mutation = _is_mutating(tool_name, arguments)
        digest = _fingerprint(tool_name, arguments) if mutation else ""
        key = f"turn:{turn}:mutation:{digest}" if turn and digest else ""
        if key and store:
            try:
                cached = store.get(key)
                if isinstance(cached, dict) and cached.get("completed"):
                    from kitt.tools.registry_core import ToolResult
                    return ToolResult(True, str(cached.get("output", "[durable mutation replay]")),
                                      metadata={"durable_replay": True, "fingerprint": digest})
            except Exception:
                pass

        result = original(tool_name, args, *pos, **kwargs)
        affected = _paths(processor, tool_name, arguments, result)
        if getattr(result, "success", False) and _is_file_mutation(tool_name, arguments) and affected:
            report = verifier.verify(affected)
            result.metadata = dict(getattr(result, "metadata", {}) or {})
            result.metadata["verification"] = report.as_dict()
            if not report.ok:
                result.success = False
                result.error = "Post-edit verification failed:\n" + report.failure_message()
        if getattr(result, "success", False) and affected:
            try:
                _invalidate_memory(processor, affected)
            except Exception:
                pass
        if key and store and getattr(result, "success", False):
            try:
                store.set(key, {"completed": True,
                    "output": str(getattr(result, "output", ""))[:12000], "paths": affected,
                    "completed_at": time.time()}, ttl_seconds=604800)
            except Exception:
                pass
        return result

    registry.execute_tool = execute_tool
    registry._agent_engineering_execute_installed = True


def install_agent_engineering(processor, registry) -> None:
    """Install all controls idempotently at KITT's existing composition seam."""
    if getattr(processor, "_agent_engineering_installed", False):
        return
    processor._agent_engineering_installed = True
    journal = TurnJournal(processor)
    processor.turn_journal = journal
    _install_tool_wrapper(processor, registry)

    original_instructions = processor._tool_instructions
    def instructions(self, enabled_tools):
        text = original_instructions(enabled_tools)
        if enabled_tools and "kitt_runtime" in enabled_tools:
            try:
                compact = str(registry.get_tool_definitions(["kitt_runtime"])[0]["args"]["operation"])
                text = re.sub(r"Supported operations:.*?(?=\nRULES:)",
                              f"Supported operations (live compact catalog): {compact}\n",
                              text, flags=re.DOTALL)
            except Exception:
                pass
        return text
    processor._tool_instructions = MethodType(instructions, processor)

    original_build = processor._build_context
    def build_context(self, cmd, task, plan, exe_profile, sf_client):
        old = getattr(self.config, "context_retrieval_token_ratio", 0.25)
        adaptive = _adaptive_ratio(self, task, cmd)
        try:
            self.config.context_retrieval_token_ratio = adaptive
            self.session_state.adaptive_retrieval_ratio = adaptive
            return original_build(cmd, task, plan, exe_profile, sf_client)
        finally:
            self.config.context_retrieval_token_ratio = old
    processor._build_context = MethodType(build_context, processor)

    original_caps = processor._routing_capabilities
    def routing_caps(self):
        caps = original_caps()
        adjusted = {}
        for name, cap in caps.items():
            try:
                feedback = _routing_feedback(self, name)
            except Exception:
                feedback = {"samples": 0}
            samples = int(feedback.get("samples", 0))
            if samples < 3:
                adjusted[name] = cap
                continue
            rate = max(0.0, min(float(feedback.get("success_rate", 0.5)), 1.0))
            weight = min(0.40, samples / 50.0)
            def blend(old):
                return max(0.05, min(1.0, float(old) * (1.0 - weight) + rate * weight))
            adjusted[name] = replace(cap,
                tool_call_reliability=blend(cap.tool_call_reliability),
                code_edit_score=blend(cap.code_edit_score),
                reasoning_score=blend(cap.reasoning_score))
        return adjusted
    processor._routing_capabilities = MethodType(routing_caps, processor)

    original_run = processor.run_turn
    def run_turn(self, cmd: TurnCommand) -> Iterator[Any]:
        journal.begin(cmd)
        terminal = False
        try:
            for event in original_run(cmd):
                journal.observe(cmd, event)
                if _EVENT_STATE.get(type(event).__name__) in _TERMINAL:
                    terminal = True
                yield event
        except BaseException as exc:
            journal.state(cmd, "FAILED", str(exc))
            raise
        finally:
            if not terminal:
                row = _get_turn(self, cmd.turn_id) or {}
                current = str(row.get("state") or "")
                if current not in {"WAITING_APPROVAL", *_TERMINAL}:
                    journal.state(cmd, "INTERRUPTED")
    processor.run_turn = MethodType(run_turn, processor)

    original_continue = processor.continue_turn
    def continue_turn(self, turn_id, grant):
        row = _get_turn(self, turn_id) or {}
        conv = str(row.get("conversation_id") or "")
        cmd = TurnCommand(conversation_id=conv, prompt="", turn_id=turn_id)
        journal.state(cmd, "EXECUTING")
        for event in original_continue(turn_id, grant):
            journal.observe(cmd, event)
            yield event
    processor.continue_turn = MethodType(continue_turn, processor)

    def resume_turn(self, turn_id: str, grant=None):
        row = _get_turn(self, turn_id)
        if not row:
            yield TurnFailed(error=f"Unknown or non-persistent turn: {turn_id}")
            return
        state = str(row.get("state") or "CREATED")
        conv = str(row.get("conversation_id") or "")
        if state == "COMPLETED":
            yield TurnCompleted(response=_turn_text(self, turn_id, "assistant") or "[Turn already completed]", edit_result=None)
            return
        if state == "WAITING_APPROVAL":
            pending = _repo(self).get_valid_pending_action(f"pa_{turn_id}", self.workspace_id)
            if pending:
                if grant is not None:
                    yield from self.continue_turn(turn_id, grant)
                    return
                yield ApprovalRequired(turn_id=turn_id, conversation_id=conv,
                    tool_name=pending.tool_name, args=pending.normalized_args,
                    action_hash=pending.action_hash, approval_request_id=pending.approval_request_id,
                    workspace_id=pending.workspace_id)
                return
        store = _store(self, conv)
        cp = store.get(f"turn:{turn_id}:checkpoint") if store else None
        if not isinstance(cp, dict):
            cp = {"prompt": _turn_text(self, turn_id, "user"), "mode": row.get("mode", "auto"), "explicit_files": []}
        prompt = str(cp.get("prompt") or "")
        if not prompt:
            yield TurnFailed(error="Turn has no persisted resumable prompt.")
            return
        yield from self.run_turn(TurnCommand(conversation_id=conv, prompt=prompt,
            mode=str(cp.get("mode") or row.get("mode") or "auto"),
            explicit_files=set(cp.get("explicit_files") or []), dry_run=bool(cp.get("dry_run", False)),
            turn_id=turn_id))
    processor.resume_turn = MethodType(resume_turn, processor)
