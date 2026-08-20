from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Optional

from kitt.artifacts.store import ArtifactStore
from kitt.children.context import narrow_child_paths
from kitt.children.messaging import ChildMessage, ChildMessageRepository
from kitt.children.repository import ChildRepository
from kitt.security.capabilities import (
    DEFAULT_CHILD_CAPABILITIES,
    compute_child_privileges,
)
from kitt.security.context import ExecutionSecurityContext


TERMINAL_CHILD_STATES = {"COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED"}
REUSABLE_CHILD_STATES = {"RETAINED", "COMPLETED", "IDLE"}


class ChildAgentManager:
    """Manage isolated retained agents, lifecycle, scope and correlated messaging."""

    def __init__(
        self,
        root_dir: str,
        repository: ChildRepository,
        artifacts: ArtifactStore,
        max_children=2,
        max_depth=1,
        workspace_id=None,
        max_worker_seconds=120.0,
        event_callback=None,
        messaging_repo=None,
        allow_peer_agent_messages=False,
        enabled=True,
    ):
        self.root = Path(root_dir).resolve()
        self.repo = repository
        self.artifacts = artifacts
        self.max_children = max_children
        self.max_depth = max_depth
        self.workspace_id = workspace_id or ""
        self.max_worker_seconds = float(max_worker_seconds)
        self._on_event = event_callback or (lambda *_: None)
        self._pool = ThreadPoolExecutor(
            max_workers=max_children, thread_name_prefix="kitt-child"
        )
        self._last_spawn_time: dict[str, float] = {}
        self.messaging = messaging_repo or ChildMessageRepository(repository.db)
        self.allow_peer_agent_messages = allow_peer_agent_messages
        self.enabled = bool(enabled)
        self._execution_lock = threading.RLock()
        self._processes: dict[str, subprocess.Popen] = {}
        self._closed = False

    def spawn(
        self,
        parent_conversation_id,
        parent_turn_id,
        name="child_task",
        task="",
        worker: Optional[Callable[[str], str]] = None,
        workspace_id=None,
        depth=1,
        model_profile="context",
        allowed_paths=None,
        enabled_tools=None,
        allowed_tools=None,
        token_budget=2048,
        timeout_seconds=120,
        security_context: Optional[ExecutionSecurityContext] = None,
        parent_capabilities=None,
    ):
        if self._closed:
            raise RuntimeError("ChildAgentManager is closed")
        if not self.enabled:
            raise PermissionError("Retained agents are disabled by runtime configuration")
        if not str(task).strip():
            raise ValueError("Child task must be non-empty")

        workspace = workspace_id or self.workspace_id
        if not workspace:
            raise ValueError("Child requires workspace_id")
        if depth > self.max_depth:
            raise ValueError("Child depth limit exceeded")

        now = time.time()
        last_spawn = self._last_spawn_time.get(parent_conversation_id, 0.0)
        if now - last_spawn < 2.0:
            raise ValueError("Child spawn rate limit: please wait 2 seconds")

        existing = self.repo.list(parent_conversation_id, 100)
        if len(existing) >= 10:
            raise ValueError("Total child spawn limit reached")
        active_count = sum(
            child.state in {"CREATED", "RUNNING", "QUEUED", "WAITING_APPROVAL"}
            for child in existing
        )
        if active_count >= self.max_children:
            raise ValueError("Child concurrency limit exceeded")

        tools = list(
            enabled_tools
            or allowed_tools
            or ["read_file", "search", "repository_map"]
        )
        parent_caps = (
            security_context.capabilities
            if security_context is not None
            else (
                parent_capabilities
                if parent_capabilities is not None
                else DEFAULT_CHILD_CAPABILITIES
            )
        )
        effective_caps = compute_child_privileges(tools, parent_caps, parent_caps)
        parent_path_scope = (
            None
            if security_context is None or security_context.path_scope is None
            else security_context.path_scope
        )
        paths = narrow_child_paths(
            self.root,
            allowed_paths or [],
            parent_path_scope,
        )

        timeout = min(float(timeout_seconds), self.max_worker_seconds)
        self._last_spawn_time[parent_conversation_id] = now
        child_security_context = (
            security_context.derive_child_context(
                child_id="pending",
                requested_capabilities=effective_caps,
                turn_id=parent_turn_id,
                workspace_policy_caps=effective_caps,
                allowed_paths=paths,
            )
            if security_context is not None
            else None
        )
        child = self.repo.create(
            parent_conversation_id,
            parent_turn_id,
            name,
            task,
            depth,
            model_profile,
            paths,
            tools,
            max(1, int(token_budget)),
            timeout,
            capabilities=effective_caps,
            security_context=(
                None if child_security_context is None else child_security_context.to_dict()
            ),
        )
        if child_security_context is not None:
            child_security_context = child_security_context.with_turn(parent_turn_id)
            payload = child_security_context.to_dict()
            payload["principal_id"] = child.id
            self.repo.update(
                child.id,
                security_context_json=json.dumps(payload, ensure_ascii=False),
            )
            child = self.repo.get(child.id)
        self.repo.update(child.id, state="QUEUED", started_at=time.time())
        self._on_event(
            "ChildAgentSpawned",
            {"child_id": child.id, "name": name, "task": task},
        )
        self._pool.submit(
            self._run_child,
            child.id,
            task,
            workspace,
            timeout,
            worker,
        )
        return self.repo.get(child.id)

    def _child_security_context(self, child) -> ExecutionSecurityContext:
        if child.security_context:
            payload = dict(child.security_context)
            payload["principal_id"] = child.id
            payload["conversation_id"] = child.parent_conversation_id
            payload["turn_id"] = child.parent_turn_id
            if (
                payload.get("fencing_subject_type") == "GOAL"
                and payload.get("fencing_subject_id")
                and payload.get("fencing_subject_conversation_id") is None
            ):
                payload["fencing_subject_conversation_id"] = (
                    child.parent_conversation_id
                )
            return ExecutionSecurityContext.from_dict(payload)
        return ExecutionSecurityContext(
            workspace_id=self.workspace_id,
            conversation_id=child.parent_conversation_id,
            turn_id=child.parent_turn_id,
            origin="AGENT",
            principal_type="CHILD",
            principal_id=child.id,
            capabilities=frozenset(child.capabilities),
            trace_id=uuid.uuid4().hex,
            parent_principal_id=child.parent_conversation_id,
            path_scope=(
                None if not child.allowed_paths else frozenset(child.allowed_paths)
            ),
        )

    def _build_run_payload(self, child, task: str) -> dict:
        security_context = self._child_security_context(child)
        return {
            "mode": "run",
            "root": str(self.root),
            "child_id": child.id,
            "runtime_conversation_id": (
                child.runtime_conversation_id or f"childconv_{child.id}"
            ),
            "task": task,
            "allowed_paths": child.allowed_paths,
            "security_context": security_context.to_dict(),
        }

    def _build_continue_payload(self, child, grant) -> dict:
        return {
            "mode": "continue",
            "root": str(self.root),
            "child_id": child.id,
            "runtime_conversation_id": child.runtime_conversation_id,
            "turn_id": child.current_task_id,
            "grant": {
                "approval_id": grant.approval_id,
                "turn_id": grant.turn_id,
                "conversation_id": grant.conversation_id,
                "workspace_id": grant.workspace_id,
                "action_hash": grant.action_hash,
                "granted_at": grant.granted_at,
                "expires_at": grant.expires_at,
                "nonce": grant.nonce,
            },
        }

    def _invoke_worker(self, child_id: str, payload: dict, timeout_seconds: float) -> dict:
        process = subprocess.Popen(
            [sys.executable, "-m", "kitt.children.worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(self.root),
            start_new_session=(sys.platform != "win32"),
        )
        with self._execution_lock:
            self._processes[child_id] = process
        try:
            try:
                stdout, stderr = process.communicate(
                    json.dumps(payload), timeout=timeout_seconds
                )
            except subprocess.TimeoutExpired:
                self._kill_tree(process)
                raise TimeoutError("child timed out")
            if process.returncode != 0:
                raise RuntimeError(
                    f"child worker exited {process.returncode}: {stderr[-2000:]}"
                )
            marker = "KITT_CHILD_RESULT:"
            line = next(
                (line for line in reversed(stdout.splitlines()) if line.startswith(marker)),
                None,
            )
            if not line:
                raise RuntimeError("child worker returned no structured result")
            data = json.loads(line[len(marker) :])
            if not isinstance(data, dict):
                raise RuntimeError("child worker returned invalid result payload")
            return data
        finally:
            with self._execution_lock:
                self._processes.pop(child_id, None)

    def _execute_worker(self, child_id: str, task: str, timeout_seconds: float) -> dict:
        child = self.repo.get(child_id)
        if not child:
            raise ValueError("Child not found")
        return self._invoke_worker(
            child_id,
            self._build_run_payload(child, task),
            timeout_seconds,
        )

    def _continue_worker(self, child_id: str, grant, timeout_seconds: float) -> dict:
        child = self.repo.get(child_id)
        if not child:
            raise ValueError("Child not found")
        return self._invoke_worker(
            child_id,
            self._build_continue_payload(child, grant),
            timeout_seconds,
        )

    def _accumulate_tokens(self, child_id: str, delta: int) -> int:
        child = self.repo.get(child_id)
        if not child:
            return 0
        total = max(0, int(child.tokens_used)) + max(0, int(delta))
        self.repo.update(child_id, tokens_used=total)
        return total

    def _mark_waiting_approval(self, child_id: str, result: dict) -> None:
        turn_id = str(result.get("turn_id") or "")
        if not turn_id:
            raise RuntimeError("WAITING_APPROVAL result is missing turn_id")
        self._accumulate_tokens(child_id, int(result.get("tokens_used", 0) or 0))
        self.repo.update(
            child_id,
            state="WAITING_APPROVAL",
            current_task_id=turn_id,
            completed_at=None,
            error=None,
        )
        self._on_event(
            "ChildAgentFinished",
            {
                "child_id": child_id,
                "status": "WAITING_APPROVAL",
                "approval_id": result.get("approval_id"),
                "turn_id": turn_id,
                "error": None,
            },
        )

    def _complete_child(self, child_id: str, workspace_id: str, result: dict) -> None:
        child = self.repo.get(child_id)
        if not child or child.state == "CANCELLED":
            return
        # A normal parent/daemon approval resume may already have completed the
        # retained child through ToolRegistry.on_approved_action_executed().
        # Do not create a duplicate artifact when the worker observes the same
        # single-use approval.
        if child.state == "COMPLETED" and child.result_artifact_id:
            return
        if not result.get("success"):
            raise RuntimeError(result.get("error", "child worker failed"))

        output = str(result.get("output", ""))
        total_tokens = self._accumulate_tokens(
            child_id, int(result.get("tokens_used", 0) or 0)
        )
        artifact = self.artifacts.put(
            workspace_id,
            output,
            "CHILD_RESULT",
            f"Result from retained child {child_id}",
            child.parent_conversation_id,
            child.parent_turn_id,
            metadata={
                "child_session_id": child_id,
                "runtime_conversation_id": child.runtime_conversation_id,
                "tokens_used": total_tokens,
            },
        )
        self.repo.update(
            child_id,
            state="COMPLETED",
            result_artifact_id=artifact.id,
            completed_at=time.time(),
            context_summary=output[-4000:],
            error=None,
        )
        self._on_event(
            "ChildAgentFinished",
            {"child_id": child_id, "status": "COMPLETED", "error": None},
        )

    def _run_child(
        self,
        child_id: str,
        task: str,
        workspace_id: str,
        timeout_seconds: float,
        worker=None,
    ) -> None:
        self.repo.update(child_id, state="RUNNING", task_started_at=time.time())
        self._on_event(
            "ChildAgentProgress",
            {
                "child_id": child_id,
                "status": "RUNNING",
                "summary": task[:80],
                "progress": 10,
            },
        )
        try:
            result = (
                {"success": True, "state": "COMPLETED", "output": worker(task), "tokens_used": 0}
                if worker is not None
                else self._execute_worker(child_id, task, timeout_seconds)
            )
            if result.get("state") == "WAITING_APPROVAL":
                self._mark_waiting_approval(child_id, result)
                return
            self._complete_child(child_id, workspace_id, result)
        except Exception as exc:
            child = self.repo.get(child_id)
            if child and child.state == "CANCELLED":
                return
            state = "TIMED_OUT" if isinstance(exc, TimeoutError) else "FAILED"
            self.repo.update(
                child_id,
                state=state,
                error=str(exc),
                completed_at=time.time(),
            )
            self._on_event(
                "ChildAgentFinished",
                {"child_id": child_id, "status": state, "error": str(exc)},
            )

    def _resume_child(self, child_id: str, workspace_id: str, grant, timeout_seconds: float) -> None:
        child = self.repo.get(child_id)
        pending_turn_id = child.current_task_id if child else None
        self.repo.update(child_id, state="RUNNING", error=None)
        try:
            result = self._continue_worker(child_id, grant, timeout_seconds)
            if result.get("state") == "WAITING_APPROVAL":
                self._mark_waiting_approval(child_id, result)
                return
            if not result.get("success"):
                raise RuntimeError(result.get("error", "child approval resume failed"))
            if not pending_turn_id:
                raise RuntimeError("Child approval resume lost pending turn id")

            # TurnProcessor.continue_turn executes the approved host action.
            # The retained agent still needs a normal model turn to consume that
            # result and finish the remainder of its task.
            self.repo.update(
                child_id,
                state="WAITING_APPROVAL",
                current_task_id=pending_turn_id,
                error=None,
            )
            self.on_approved_action_executed(
                child_id,
                pending_turn_id,
                str(result.get("output", "")),
            )
        except Exception as exc:
            child = self.repo.get(child_id)
            if child and child.state == "CANCELLED":
                return
            state = "TIMED_OUT" if isinstance(exc, TimeoutError) else "FAILED"
            self.repo.update(
                child_id,
                state=state,
                error=str(exc),
                completed_at=time.time(),
            )
            self._on_event(
                "ChildAgentFinished",
                {"child_id": child_id, "status": state, "error": str(exc)},
            )

    @staticmethod
    def _kill_tree(process) -> None:
        if process.poll() is not None:
            return
        try:
            if sys.platform != "win32":
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=1.5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
            else:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
        finally:
            try:
                process.kill()
            except Exception:
                pass

    def inspect(self, child_id, conversation_id=None, workspace_id=None):
        return self.repo.get_scoped(child_id, conversation_id, workspace_id)

    def list(self, parent_conversation_id, limit=20):
        return self.repo.list(parent_conversation_id, limit)

    def cancel(self, child_id, conversation_id=None, workspace_id=None):
        child = self.inspect(child_id, conversation_id, workspace_id)
        if not child or child.state in TERMINAL_CHILD_STATES:
            return False
        self.repo.update(
            child_id,
            state="CANCELLED",
            error="cancelled by user",
            completed_at=time.time(),
        )
        with self._execution_lock:
            process = self._processes.get(child_id)
        if process:
            self._kill_tree(process)
        return True

    def wait(self, child_id, timeout=60.0):
        deadline = time.monotonic() + max(0.1, timeout)
        while time.monotonic() < deadline:
            child = self.repo.get(child_id)
            if child and child.state in TERMINAL_CHILD_STATES | {"WAITING_APPROVAL"}:
                return child
            time.sleep(0.02)
        return self.repo.get(child_id)

    def retain(self, child_id, conversation_id=None, workspace_id=None):
        child = self.inspect(child_id, conversation_id, workspace_id)
        if not child:
            return False
        if child.state not in {"COMPLETED", "IDLE", "RETAINED"}:
            raise ValueError(f"Cannot retain child in state {child.state}")
        self.repo.update(child_id, state="RETAINED")
        self._on_event(
            "ChildAgentRetained", {"child_id": child_id, "name": child.name}
        )
        return True

    def assign_task(
        self,
        child_id,
        task,
        workspace_id=None,
        timeout_seconds=120.0,
        worker=None,
        conversation_id=None,
    ):
        child = self.inspect(
            child_id, conversation_id, workspace_id or self.workspace_id
        )
        if not child:
            raise ValueError("Child not found")
        if child.state not in REUSABLE_CHILD_STATES:
            raise ValueError(f"Cannot assign task in state {child.state}")
        if child.tokens_used >= child.token_budget:
            raise ValueError("Child token budget exhausted")
        if not str(task).strip():
            raise ValueError("Child task must be non-empty")

        timeout = min(float(timeout_seconds), self.max_worker_seconds)
        self.repo.update(
            child_id,
            state="QUEUED",
            task=task,
            error=None,
            started_at=time.time(),
            completed_at=None,
            current_task_id=f"task_{uuid.uuid4().hex}",
            task_started_at=time.time(),
        )
        self._pool.submit(
            self._run_child,
            child_id,
            task,
            workspace_id or self.workspace_id,
            timeout,
            worker,
        )
        return self.repo.get(child_id)

    def resume_after_approval(
        self,
        child_id: str,
        grant,
        *,
        conversation_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ):
        child = self.inspect(
            child_id,
            conversation_id,
            workspace_id or self.workspace_id,
        )
        if not child:
            raise ValueError("Child not found")
        if child.state != "WAITING_APPROVAL":
            raise ValueError(f"Child is not waiting for approval: {child.state}")
        if not child.current_task_id:
            raise ValueError("Child has no pending turn to resume")
        if grant.turn_id != child.current_task_id:
            raise PermissionError("Approval grant does not match child pending turn")
        if grant.conversation_id != child.runtime_conversation_id:
            raise PermissionError("Approval grant does not match child conversation")
        if grant.workspace_id != (workspace_id or self.workspace_id):
            raise PermissionError("Approval grant does not match child workspace")

        timeout = min(
            float(timeout_seconds or child.timeout_seconds), self.max_worker_seconds
        )
        self.repo.update(child_id, state="QUEUED", error=None, completed_at=None)
        self._pool.submit(
            self._resume_child,
            child_id,
            workspace_id or self.workspace_id,
            grant,
            timeout,
        )
        return self.repo.get(child_id)

    def on_approved_action_executed(
        self, child_id: str, turn_id: str, output: str
    ) -> bool:
        """Continue a retained child after an approved action succeeds.

        The approved host action has already executed in the parent runtime.
        Starting a fresh continuation turn avoids replaying the approved action
        while preserving the retained child conversation/history. Completion is
        recorded only after that continuation turn actually finishes.
        """
        child = self.repo.get(child_id)
        if (
            not child
            or child.state != "WAITING_APPROVAL"
            or child.current_task_id != turn_id
        ):
            return False
        if self._closed:
            return False
        if not self.workspace_id:
            raise ValueError("Child manager has no workspace_id")
        if child.tokens_used >= child.token_budget:
            self.repo.update(
                child_id,
                state="FAILED",
                error="Child token budget exhausted after approved action",
                completed_at=time.time(),
            )
            return False

        approved_output = str(output or "")[:8000]
        continuation_task = (
            "Continue the retained task after an approved host action. "
            "The approved action has already succeeded; do not repeat it. "
            "Use the retained conversation/history and finish the remaining work.\n\n"
            f"Approved host result:\n{approved_output}\n\n"
            f"Original retained task:\n{child.task}"
        )
        continuation_id = f"resume_{uuid.uuid4().hex}"
        self.repo.update(
            child_id,
            state="QUEUED",
            current_task_id=continuation_id,
            completed_at=None,
            error=None,
            context_summary=approved_output[-4000:],
        )
        self._on_event(
            "ChildAgentApprovalContinued",
            {
                "child_id": child_id,
                "approved_turn_id": turn_id,
                "continuation_id": continuation_id,
            },
        )
        timeout = min(float(child.timeout_seconds), self.max_worker_seconds)
        self._pool.submit(
            self._run_child,
            child_id,
            continuation_task,
            self.workspace_id,
            timeout,
            None,
        )
        return True

    def send_message(
        self,
        conversation_id,
        parent_id,
        child_id,
        sender_id,
        recipient_id,
        payload,
        kind="DIRECT",
        correlation_id=None,
        reply_to=None,
        trace_id=None,
    ):
        child = self.repo.get_scoped(child_id, conversation_id, self.workspace_id)
        if not child:
            raise ValueError("Child not found")
        if sender_id != parent_id and recipient_id != parent_id:
            if not self.allow_peer_agent_messages:
                raise PermissionError("Peer agent messages disabled")
            sender = self.repo.get_scoped(
                sender_id, conversation_id, self.workspace_id
            )
            recipient = self.repo.get_scoped(
                recipient_id, conversation_id, self.workspace_id
            )
            if not sender or not recipient:
                raise PermissionError("Peer child scope mismatch")
        message = self.messaging.send(
            conversation_id=conversation_id,
            parent_id=parent_id,
            child_id=child_id,
            sender_id=sender_id,
            recipient_id=recipient_id,
            payload=payload,
            kind=kind,
            correlation_id=correlation_id,
            reply_to=reply_to,
            trace_id=trace_id,
        )
        self._on_event(
            "ChildAgentMessageSent",
            {
                "message_id": message.id,
                "kind": kind,
                "correlation_id": correlation_id,
            },
        )
        return message

    def list_messages(self, conversation_id, child_id=None, limit=50):
        if child_id:
            child = self.repo.get_scoped(
                child_id, conversation_id, self.workspace_id
            )
            if not child:
                raise ValueError("Child not found")
        return self.messaging.list_messages(
            conversation_id, child_id=child_id, limit=limit
        )

    def ask(self, child_id, question, timeout=30.0):
        child = self.repo.get_scoped(child_id, workspace_id=self.workspace_id)
        if not child:
            raise ValueError("Child not found")
        correlation_id = f"corr_{uuid.uuid4().hex}"
        original = self.send_message(
            child.parent_conversation_id,
            child.parent_conversation_id,
            child.id,
            child.parent_conversation_id,
            child.id,
            {"question": question},
            kind="ASK",
            correlation_id=correlation_id,
        )

        def answer() -> None:
            try:
                result = self._execute_worker(
                    child.id,
                    "Parent asks a retained specialist question. "
                    f"Answer directly using retained context:\n{question}",
                    min(float(timeout), child.timeout_seconds),
                )
                if result.get("state") == "WAITING_APPROVAL":
                    self._mark_waiting_approval(child.id, result)
                    payload = {
                        "status": "WAITING_APPROVAL",
                        "approval_id": result.get("approval_id"),
                    }
                elif result.get("success"):
                    payload = {"answer": str(result.get("output", ""))}
                else:
                    payload = {"error": result.get("error", "child worker failed")}
                self.reply(child.id, original, payload)
            except Exception as exc:
                self.reply(child.id, original, {"error": str(exc)})

        self._pool.submit(answer)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            replies = self.messaging.list_replies(correlation_id)
            if replies:
                return {
                    "status": "ANSWERED",
                    "reply": replies[0].payload,
                    "correlation_id": correlation_id,
                }
            time.sleep(0.05)
        return {
            "status": "SENT",
            "correlation_id": correlation_id,
            "message_id": original.id,
        }

    def reply(self, child_id, original_message: ChildMessage, payload):
        child = self.repo.get_scoped(
            child_id, original_message.conversation_id, self.workspace_id
        )
        if not child:
            raise ValueError("Child not found")
        return self.send_message(
            child.parent_conversation_id,
            child.parent_conversation_id,
            child.id,
            child.id,
            original_message.sender_id,
            payload,
            kind="REPLY",
            correlation_id=original_message.correlation_id,
            reply_to=original_message.id,
        )

    def broadcast(self, parent_conversation_id, payload):
        return [
            self.send_message(
                parent_conversation_id,
                parent_conversation_id,
                child.id,
                parent_conversation_id,
                child.id,
                payload,
                kind="BROADCAST",
            )
            for child in self.list(parent_conversation_id, 50)
            if child.state in {"RUNNING", "QUEUED", "RETAINED", "IDLE"}
        ]

    def shutdown_all(self) -> None:
        with self._execution_lock:
            processes = list(self._processes.items())
        for child_id, process in processes:
            self._kill_tree(process)
            child = self.repo.get(child_id)
            if child and child.state not in TERMINAL_CHILD_STATES:
                self.repo.update(
                    child_id,
                    state="CANCELLED",
                    error="cancelled during shutdown",
                    completed_at=time.time(),
                )

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.shutdown_all()
        self._pool.shutdown(wait=False, cancel_futures=True)
