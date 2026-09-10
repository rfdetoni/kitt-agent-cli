"""KITT-native cross-cutting agent engineering controls.

This module composes existing KITT services at the registry/processor boundary.
It intentionally does not own a second runtime, scheduler, memory store, or
agent framework.  The installer is idempotent and keeps the original
TurnProcessor implementation as the execution engine.
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
    "TurnStarted": "RUNNING",
    "FilterCompleted": "FILTERING",
    "ModelSelected": "ROUTING",
    "ContextBuildCompleted": "RETRIEVING",
    "ContextResolved": "RETRIEVING",
    "BudgetApplied": "READY",
    "ThinkingStarted": "EXECUTING",
    "ToolCallProposed": "EXECUTING",
    "ToolStarted": "EXECUTING",
    "ToolCompleted": "EXECUTING",
    "ApprovalRequired": "WAITING_APPROVAL",
    "EditApplied": "VALIDATING",
    "MetricsRecorded": "VALIDATING",
    "TurnCompleted": "COMPLETED",
    "TurnFailed": "FAILED",
    "TurnBlocked": "BLOCKED",
    "TurnCancelled": "CANCELLED",
}

_MUTATING_LEGACY = {
    "write_file",
    "apply_patch",
    "run_command",
    "child_spawn",
    "goal_create",
    "goal_add_gate",
    "harness_remember",
    "queue_input",
}
_FILE_MUTATIONS = {"write_file", "apply_patch"}


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
    raw = json.dumps(
        [tool_name, _safe_json(args)],
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _runtime_store(processor, conversation_id: str) -> RuntimeStateStore | None:
    history = getattr(processor, "history_service", None)
    repo = getattr(history, "repo", None)
    db = getattr(repo, "db", None)
    if db is None or not conversation_id:
        return None
    try:
        return RuntimeStateStore(db, processor.workspace_id, conversation_id)
    except Exception:
        return None


def _turn_repo(processor):
    history = getattr(processor, "history_service", None)
    return getattr(history, "repo", None)


def _event_payload(event: Any) -> dict[str, Any]:
    if hasattr(event, "__dict__"):
        return {k: _safe_json(v) for k, v in vars(event).items() if not k.startswith("_")}
    return {}


class TurnJournal:
    """Persist the operational lifecycle using the existing turns/runtime_states."""

    def __init__(self, processor):
        self.processor = processor
        self.selected_profiles: dict[str, str] = {}
        self.started_at: dict[str, float] = {}

    def begin(self, cmd: TurnCommand) -> None:
        repo = _turn_repo(self.processor)
        if repo is not None and hasattr(repo, "ensure_turn"):
            repo.ensure_turn(cmd.conversation_id, cmd.turn_id, mode=cmd.mode)
        store = _runtime_store(self.processor, cmd.conversation_id)
        if store is not None:
            try:
                store.set(
                    f"turn:{cmd.turn_id}:checkpoint",
                    {
                        "turn_id": cmd.turn_id,
                        "conversation_id": cmd.conversation_id,
                        "prompt": cmd.prompt,
                        "mode": cmd.mode,
                        "explicit_files": sorted(cmd.explicit_files),
                        "dry_run": bool(cmd.dry_run),
                        "state": "RUNNING",
                        "updated_at": time.time(),
                    },
                    ttl_seconds=7 * 24 * 3600,
                )
            except Exception:
                pass
        self.started_at[cmd.turn_id] = time.time()
        self.state(cmd, "RUNNING")

    def state(self, cmd: TurnCommand, state: str, *, error: str | None = None) -> None:
        repo = _turn_repo(self.processor)
        task = getattr(getattr(self.processor, "session_state", None), "last_task", None)
        if repo is not None and hasattr(repo, "update_turn_state"):
            try:
                repo.update_turn_state(
                    cmd.turn_id,
                    cmd.conversation_id,
                    state,
                    semantic_intent=getattr(task, "intent", None),
                    risk=getattr(task, "risk", None),
                    confidence=getattr(task, "confidence", None),
                    error_code=(str(error)[:512] if error else None),
                )
            except Exception:
                pass
        store = _runtime_store(self.processor, cmd.conversation_id)
        if store is not None:
            try:
                key = f"turn:{cmd.turn_id}:checkpoint"
                value = store.get(key) or {}
                if isinstance(value, dict):
                    value.update({"state": state, "updated_at": time.time()})
                    if error:
                        value["error"] = str(error)[:2000]
                    store.set(key, value, ttl_seconds=7 * 24 * 3600)
            except Exception:
                pass
        try:
            self.processor._emit(
                "TurnStateChanged",
                {
                    "turn_id": cmd.turn_id,
                    "conversation_id": cmd.conversation_id,
                    "state": state,
                },
            )
        except Exception:
            pass

    def observe(self, cmd: TurnCommand, event: Any) -> None:
        name = type(event).__name__
        state = _EVENT_STATE.get(name)
        if name == "ModelSelected":
            profile = str(getattr(event, "profile_name", "") or "")
            if profile:
                self.selected_profiles[cmd.turn_id] = profile
        if state:
            error = getattr(event, "error", None) if state == "FAILED" else None
            self.state(cmd, state, error=error)
        if state in _TERMINAL:
            self._record_routing_outcome(cmd, state)

    def _record_routing_outcome(self, cmd: TurnCommand, state: str) -> None:
        repo = _turn_repo(self.processor)
        profile = self.selected_profiles.pop(cmd.turn_id, "")
        started = self.started_at.pop(cmd.turn_id, time.time())
        if not profile or repo is None or not hasattr(repo, "save_routing_outcome"):
            return
        try:
            repo.save_routing_outcome(
                cmd.conversation_id,
                cmd.turn_id,
                profile,
                success=(state == "COMPLETED"),
                duration_ms=max(0.0, (time.time() - started) * 1000.0),
            )
        except Exception:
            pass


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
    if tool_name in _FILE_MUTATIONS:
        return True
    return tool_name == "kitt_runtime" and str(args.get("operation") or "") in {
        "repo.edit_symbol",
        "patch.apply",
    }


def _affected_paths(processor, tool_name: str, args: dict[str, Any], result: Any) -> list[str]:
    try:
        paths = processor._paths_from_tool(tool_name, args, result)
        if paths:
            return list(dict.fromkeys(str(p) for p in paths if p))
    except Exception:
        pass
    inner = args.get("arguments", {}) if tool_name == "kitt_runtime" else args
    if isinstance(inner, dict):
        path = inner.get("path") or inner.get("file")
        if path:
            return [str(path)]
        raw = inner.get("paths")
        if isinstance(raw, list):
            return [str(p) for p in raw[:64] if p]
    return []


def _invalidate_memory(processor, paths: list[str]) -> None:
    if not paths:
        return
    memory = getattr(processor, "memory", None)
    repo = getattr(memory, "memory_repo", None)
    if repo is None:
        try:
            from kitt.dreaming.repository import MemoryRepository
            history_repo = _turn_repo(processor)
            if history_repo is not None:
                repo = MemoryRepository(history_repo.db)
        except Exception:
            repo = None
    if repo is not None and hasattr(repo, "invalidate_for_paths"):
        try:
            repo.invalidate_for_paths(processor.workspace_id, paths)
        except Exception:
            pass


def _adaptive_ratio(processor, task: Any, cmd: TurnCommand) -> float:
    base = float(getattr(processor.config, "context_retrieval_token_ratio", 0.25))
    ratio = base
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


def _install_tool_wrapper(processor, registry) -> None:
    if getattr(registry, "_agent_engineering_execute_installed", False):
        return
    original = registry.execute_tool
    verifier = VerificationOrchestrator(registry.root_path, registry.process_runner)

    def execute_tool(tool_name, args, *pos, **kwargs):
        arguments = args if isinstance(args, dict) else {}
        conversation_id = str(kwargs.get("conversation_id") or "")
        turn_id = str(kwargs.get("turn_id") or "")
        store = _runtime_store(processor, conversation_id)
        mutation = _is_mutating(tool_name, arguments)
        digest = _fingerprint(tool_name, arguments) if mutation else ""
        replay_key = f"turn:{turn_id}:mutation:{digest}" if turn_id and digest else ""
        if replay_key and store is not None:
            try:
                cached = store.get(replay_key)
                if isinstance(cached, dict) and cached.get("completed"):
                    from kitt.tools.registry_core import ToolResult
                    return ToolResult(
                        bool(cached.get("success", True)),
                        str(cached.get("output", "[durable mutation replay]")),
                        error=cached.get("error"),
                        metadata={"durable_replay": True, "fingerprint": digest},
                    )
            except Exception:
                pass

        result = original(tool_name, args, *pos, **kwargs)
        paths = _affected_paths(processor, tool_name, arguments, result)

        if getattr(result, "success", False) and _is_file_mutation(tool_name, arguments) and paths:
            report = verifier.verify(paths)
            metadata = dict(getattr(result, "metadata", {}) or {})
            metadata["verification"] = report.as_dict()
            result.metadata = metadata
            if not report.ok:
                # Feeding the failure back through the normal tool loop is the
                # repair loop: the model receives deterministic diagnostics,
                # edits again, and this same gate re-runs before success.
                result.success = False
                result.error = "Post-edit verification failed:\n" + report.failure_message()

        if getattr(result, "success", False) and paths:
            _invalidate_memory(processor, paths)

        if replay_key and store is not None and getattr(result, "success", False):
            try:
                store.set(
                    replay_key,
                    {
                        "completed": True,
                        "success": True,
                        "output": str(getattr(result, "output", ""))[:12000],
                        "error": getattr(result, "error", None),
                        "paths": paths,
                        "completed_at": time.time(),
                    },
                    ttl_seconds=7 * 24 * 3600,
                )
            except Exception:
                pass
        return result

    registry.execute_tool = execute_tool
    registry._agent_engineering_execute_installed = True


def install_agent_engineering(processor, registry) -> None:
    """Install KITT-native reliability/intelligence controls exactly once."""
    if getattr(processor, "_agent_engineering_installed", False):
        return
    processor._agent_engineering_installed = True
    journal = TurnJournal(processor)
    processor.turn_journal = journal

    _install_tool_wrapper(processor, registry)

    original_tool_instructions = processor._tool_instructions
    def tool_instructions(self, enabled_tools):
        text = original_tool_instructions(enabled_tools)
        if enabled_tools and "kitt_runtime" in enabled_tools:
            try:
                defs = registry.get_tool_definitions(["kitt_runtime"])
                compact = str(defs[0]["args"]["operation"])
                text = re.sub(
                    r"Supported operations:.*?(?=\nRULES:)",
                    f"Supported operations (live compact catalog): {compact}\n",
                    text,
                    flags=re.DOTALL,
                )
            except Exception:
                pass
        return text
    processor._tool_instructions = MethodType(tool_instructions, processor)

    original_build_context = processor._build_context
    def build_context(self, cmd, task, plan, exe_profile, sf_client):
        old = getattr(self.config, "context_retrieval_token_ratio", 0.25)
        adaptive = _adaptive_ratio(self, task, cmd)
        try:
            self.config.context_retrieval_token_ratio = adaptive
            self.session_state.adaptive_retrieval_ratio = adaptive
            return original_build_context(cmd, task, plan, exe_profile, sf_client)
        finally:
            self.config.context_retrieval_token_ratio = old
    processor._build_context = MethodType(build_context, processor)

    original_caps = processor._routing_capabilities
    def routing_capabilities(self):
        caps = original_caps()
        repo = _turn_repo(self)
        if repo is None or not hasattr(repo, "get_routing_feedback"):
            return caps
        adjusted = {}
        for name, cap in caps.items():
            try:
                feedback = repo.get_routing_feedback(name)
            except Exception:
                feedback = None
            samples = int((feedback or {}).get("samples", 0))
            if samples < 3:
                adjusted[name] = cap
                continue
            success_rate = max(0.0, min(float(feedback.get("success_rate", 0.5)), 1.0))
            weight = min(0.40, samples / 50.0)
            def blend(old):
                return max(0.05, min(1.0, float(old) * (1.0 - weight) + success_rate * weight))
            adjusted[name] = replace(
                cap,
                tool_call_reliability=blend(cap.tool_call_reliability),
                code_edit_score=blend(cap.code_edit_score),
                reasoning_score=blend(cap.reasoning_score),
            )
        return adjusted
    processor._routing_capabilities = MethodType(routing_capabilities, processor)

    original_run_turn = processor.run_turn
    def run_turn(self, cmd: TurnCommand) -> Iterator[Any]:
        journal.begin(cmd)
        terminal_seen = False
        try:
            for event in original_run_turn(cmd):
                journal.observe(cmd, event)
                if _EVENT_STATE.get(type(event).__name__) in _TERMINAL:
                    terminal_seen = True
                yield event
        except BaseException as exc:
            journal.state(cmd, "FAILED", error=str(exc))
            raise
        finally:
            # Generator abandonment is a durable interruption, not success.
            if not terminal_seen:
                repo = _turn_repo(self)
                row = repo.get_turn(cmd.turn_id) if repo is not None and hasattr(repo, "get_turn") else None
                current = str((row or {}).get("state") or "")
                if current not in {"WAITING_APPROVAL", *list(_TERMINAL)}:
                    journal.state(cmd, "INTERRUPTED")
    processor.run_turn = MethodType(run_turn, processor)

    original_continue = processor.continue_turn
    def continue_turn(self, turn_id, grant):
        repo = _turn_repo(self)
        row = repo.get_turn(turn_id) if repo is not None and hasattr(repo, "get_turn") else None
        conv = str((row or {}).get("conversation_id") or "")
        cmd = TurnCommand(conversation_id=conv, prompt="", turn_id=turn_id)
        journal.state(cmd, "EXECUTING")
        for event in original_continue(turn_id, grant):
            journal.observe(cmd, event)
            yield event
    processor.continue_turn = MethodType(continue_turn, processor)

    def resume_turn(self, turn_id: str, grant=None):
        repo = _turn_repo(self)
        if repo is None or not hasattr(repo, "get_turn"):
            yield TurnFailed(error="Durable resume requires history persistence.")
            return
        row = repo.get_turn(turn_id)
        if not row:
            yield TurnFailed(error=f"Unknown turn: {turn_id}")
            return
        state = str(row.get("state") or "CREATED")
        conv = str(row.get("conversation_id") or "")
        if state == "COMPLETED":
            response = repo.get_turn_assistant_response(turn_id) if hasattr(repo, "get_turn_assistant_response") else ""
            yield TurnCompleted(response=response or "[Turn already completed]", edit_result=None)
            return
        if state == "WAITING_APPROVAL":
            pending = repo.get_valid_pending_action(f"pa_{turn_id}", self.workspace_id)
            if pending is not None:
                if grant is not None:
                    yield from self.continue_turn(turn_id, grant)
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
        store = _runtime_store(self, conv)
        checkpoint = store.get(f"turn:{turn_id}:checkpoint") if store is not None else None
        if not isinstance(checkpoint, dict):
            prompt = repo.get_turn_user_prompt(turn_id) if hasattr(repo, "get_turn_user_prompt") else ""
            checkpoint = {"prompt": prompt, "mode": row.get("mode", "auto"), "explicit_files": []}
        prompt = str(checkpoint.get("prompt") or "")
        if not prompt:
            yield TurnFailed(error="Turn has no persisted resumable prompt.")
            return
        cmd = TurnCommand(
            conversation_id=conv,
            prompt=prompt,
            mode=str(checkpoint.get("mode") or row.get("mode") or "auto"),
            explicit_files=set(checkpoint.get("explicit_files") or []),
            dry_run=bool(checkpoint.get("dry_run", False)),
            turn_id=turn_id,
        )
        yield from self.run_turn(cmd)
    processor.resume_turn = MethodType(resume_turn, processor)
