"""KITT-native durable execution, adaptive context, learned routing and verification.

This is a composition adapter over KITT's existing TurnProcessor and ToolRegistry.
It owns no second runtime, scheduler, persistence layer or external framework.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import fields, is_dataclass, replace
from types import MethodType
from typing import Any, Iterator

from kitt.core.turn_command import TurnCommand
from kitt.core.turn_events import ApprovalRequired, TurnCompleted, TurnFailed
from kitt.evidence.episodes import TaskEpisodeService
from kitt.evidence.invariants import RuntimeInvariantService
from kitt.evidence.ledger import SessionLedger
from kitt.evidence.projections import build_default_projection_registry
from kitt.runtime.state import RuntimeStateStore
from kitt.validation.orchestrator import VerificationOrchestrator

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
        self.db = _db(processor)
        self.ledger = None
        self.episodes = None
        self.invariants = None
        if self.db is not None:
            projections = build_default_projection_registry(self.db)
            self.ledger = SessionLedger(self.db, projections)
            self.episodes = TaskEpisodeService(
                self.db,
                self.ledger,
                getattr(getattr(processor, "registry", None), "goal_service", None),
            )
            self.invariants = RuntimeInvariantService(self.db)

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

    def begin(self, cmd: TurnCommand) -> None:
        persistent = _ensure_turn(self.processor, cmd)
        self.started[cmd.turn_id] = time.time()
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

    def observe(self, cmd: TurnCommand, event: Any) -> None:
        name = type(event).__name__
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

        if name == "ModelSelected":
            self.profiles[cmd.turn_id] = str(getattr(event, "profile_name", "") or "")
        if state:
            self.state(cmd, state, getattr(event, "error", None) if state == "FAILED" else None)
        if state in TERMINAL:
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
            if self.invariants is not None:
                try:
                    self.invariants.check_terminal(cmd.conversation_id, cmd.turn_id)
                except Exception:
                    if self.invariants.mode == "STRICT":
                        raise



def _install_tool_execution(processor, registry) -> None:
    if getattr(registry, "_agent_engineering_execute_installed", False):
        return
    original = registry.execute_tool
    verifier = VerificationOrchestrator(registry.root_path, registry.process_runner)

    def execute_tool(name, args, *pos, **kwargs):
        arguments = args if isinstance(args, dict) else {}
        conv = str(kwargs.get("conversation_id") or "")
        turn = str(kwargs.get("turn_id") or "")
        store = _state_store(processor, conv)
        digest = _fingerprint(name, arguments) if _is_mutating(name, arguments) else ""
        replay_key = f"turn:{turn}:mutation:{digest}" if turn and digest else ""
        if replay_key and store:
            try:
                cached = store.get(replay_key)
                if isinstance(cached, dict) and cached.get("completed"):
                    from kitt.tools.registry_core import ToolResult
                    return ToolResult(True, str(cached.get("output", "[durable mutation replay]")),
                                      metadata={"durable_replay": True, "fingerprint": digest})
            except Exception:
                pass

        result = original(name, args, *pos, **kwargs)
        paths = _affected_paths(processor, name, arguments, result)
        if getattr(result, "success", False) and _file_mutation(name, arguments) and paths:
            report = verifier.verify(paths)
            result.metadata = dict(getattr(result, "metadata", {}) or {})
            result.metadata["verification"] = report.as_dict()
            if not report.ok:
                result.success = False
                result.error = "Post-edit verification failed:\n" + report.failure_message()
        if getattr(result, "success", False) and paths:
            _expire_code_memory(processor, paths)
            journal = getattr(processor, "turn_journal", None)
            if journal is not None:
                inner_operation = (
                    str(arguments.get("operation") or name)
                    if name == "kitt_runtime" and isinstance(arguments, dict)
                    else name
                )
                journal.record_deliverables(turn, paths, kind=inner_operation)
        if replay_key and store and getattr(result, "success", False):
            try:
                store.set(replay_key, {"completed": True,
                    "output": str(getattr(result, "output", ""))[:12000], "paths": paths,
                    "completed_at": time.time()}, ttl_seconds=604800)
            except Exception:
                pass
        return result

    registry.execute_tool = execute_tool
    registry._agent_engineering_execute_installed = True


def install_agent_engineering(processor, registry) -> None:
    """Install all controls exactly once at the existing registry seam."""
    if getattr(processor, "_agent_engineering_installed", False):
        return
    processor._agent_engineering_installed = True
    journal = DurableTurnJournal(processor)
    processor.turn_journal = journal
    processor._record_model_request = journal.record_model_request
    processor.session_ledger = journal.ledger
    processor.session_projections = journal.ledger.projections if journal.ledger else None
    processor.task_episodes = journal.episodes
    processor.runtime_invariants = journal.invariants
    _install_tool_execution(processor, registry)

    # Register native turn hooks instead of stacking MethodType wrappers for
    # event correlation, adaptive retrieval and learned routing.
    processor._agent_trace_context = None
    processor._adaptive_retrieval_ratio_fn = adaptive_retrieval_ratio
    processor._routing_feedback_snapshot_fn = routing_feedback_snapshot

    original_run = processor.run_turn
    def run_turn(self, cmd: TurnCommand) -> Iterator[Any]:
        journal.begin(cmd)
        terminal = False
        previous_context = getattr(self, "_agent_trace_context", None)
        self._agent_trace_context = (cmd.turn_id, cmd.conversation_id)
        try:
            for event in original_run(cmd):
                journal.observe(cmd, event)
                if EVENT_STATE.get(type(event).__name__) in TERMINAL:
                    terminal = True
                yield event
        except BaseException as exc:
            journal.state(cmd, "FAILED", str(exc))
            raise
        finally:
            self._agent_trace_context = previous_context
            if not terminal:
                current = str((_turn(self, cmd.turn_id) or {}).get("state") or "")
                if current not in {"WAITING_APPROVAL", *TERMINAL}:
                    journal.state(cmd, "INTERRUPTED")
    processor.run_turn = MethodType(run_turn, processor)

    original_continue = processor.continue_turn
    def continue_turn(self, turn_id, grant):
        row = _turn(self, turn_id) or {}
        cmd = TurnCommand(conversation_id=str(row.get("conversation_id") or ""), prompt="", turn_id=turn_id)
        previous_context = getattr(self, "_agent_trace_context", None)
        self._agent_trace_context = (cmd.turn_id, cmd.conversation_id)
        journal.state(cmd, "EXECUTING")
        try:
            for event in original_continue(turn_id, grant):
                journal.observe(cmd, event)
                yield event
        finally:
            self._agent_trace_context = previous_context
    processor.continue_turn = MethodType(continue_turn, processor)

    def resume_turn(self, turn_id: str, grant=None):
        row = _turn(self, turn_id)
        if not row:
            yield TurnFailed(error=f"Unknown or non-persistent turn: {turn_id}")
            return
        state = str(row.get("state") or "CREATED")
        conv = str(row.get("conversation_id") or "")
        if state == "COMPLETED":
            yield TurnCompleted(response=_message(self, turn_id, "assistant") or "[Turn already completed]", edit_result=None)
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
        store = _state_store(self, conv)
        checkpoint = store.get(f"turn:{turn_id}:checkpoint") if store else None
        if not isinstance(checkpoint, dict):
            checkpoint = {"prompt": _message(self, turn_id, "user"), "mode": row.get("mode", "auto"), "explicit_files": []}
        prompt = str(checkpoint.get("prompt") or "")
        if not prompt:
            yield TurnFailed(error="Turn has no persisted resumable prompt.")
            return
        yield from self.run_turn(TurnCommand(conversation_id=conv, prompt=prompt,
            mode=str(checkpoint.get("mode") or row.get("mode") or "auto"),
            explicit_files=set(checkpoint.get("explicit_files") or []),
            dry_run=bool(checkpoint.get("dry_run", False)), turn_id=turn_id))
    processor.resume_turn = MethodType(resume_turn, processor)
