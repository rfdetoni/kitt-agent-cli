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
from typing import Callable, List, Optional, Any

from kitt.artifacts.store import ArtifactStore
from kitt.children.context import validate_child_paths
from kitt.children.repository import ChildRepository
from kitt.children.messaging import ChildMessageRepository, ChildMessage
from kitt.security.capabilities import compute_child_privileges, DEFAULT_CHILD_CAPABILITIES
from kitt.security.context import ExecutionSecurityContext


class ChildAgentManager:
    def __init__(self, root_dir: str, repository: ChildRepository, artifacts: ArtifactStore,
                 max_children=2, max_depth=1, workspace_id=None, max_worker_seconds=120.0,
                 event_callback=None, messaging_repo=None, allow_peer_agent_messages=False,
                 enabled=True):
        self.root = Path(root_dir).resolve()
        self.repo = repository
        self.artifacts = artifacts
        self.max_children = max_children
        self.max_depth = max_depth
        self.workspace_id = workspace_id or ""
        self.max_worker_seconds = max_worker_seconds
        self._on_event = event_callback or (lambda *_: None)
        self._pool = ThreadPoolExecutor(max_workers=max_children, thread_name_prefix="kitt-child")
        self._last_spawn_time = {}
        self.messaging = messaging_repo or ChildMessageRepository(repository.db)
        self.allow_peer_agent_messages = allow_peer_agent_messages
        self.enabled = bool(enabled)
        self._execution_lock = threading.RLock()
        self._processes: dict[str, subprocess.Popen] = {}

    def spawn(self, parent_conversation_id, parent_turn_id, name="child_task", task="",
              worker: Optional[Callable[[str], str]] = None, workspace_id=None, depth=1,
              model_profile="context", allowed_paths=None, enabled_tools=None,
              allowed_tools=None, token_budget=2048, timeout_seconds=120,
              security_context: Optional[ExecutionSecurityContext] = None,
              parent_capabilities=None):
        if not self.enabled:
            raise PermissionError("Retained agents are disabled by runtime configuration")
        if not task.strip():
            raise ValueError("Child task must be non-empty")
        ws_id = workspace_id or self.workspace_id
        if not ws_id:
            raise ValueError("Child requires workspace_id")
        if depth > self.max_depth:
            raise ValueError("Child depth limit exceeded")

        now = time.time()
        last = self._last_spawn_time.get(parent_conversation_id, 0)
        if now - last < 2:
            raise ValueError("Child spawn rate limit: please wait 2 seconds")
        existing = self.repo.list(parent_conversation_id, 100)
        if len(existing) >= 10:
            raise ValueError("Total child spawn limit reached")
        if sum(c.state in {"CREATED", "RUNNING", "QUEUED"} for c in existing) >= self.max_children:
            raise ValueError("Child concurrency limit exceeded")

        tools = list(enabled_tools or allowed_tools or ["read_file", "search", "repository_map"])
        parent_caps = (
            security_context.capabilities if security_context is not None
            else (parent_capabilities if parent_capabilities is not None else DEFAULT_CHILD_CAPABILITIES)
        )
        effective_caps = compute_child_privileges(tools, parent_caps, parent_caps)
        paths = validate_child_paths(self.root, allowed_paths or [])
        self._last_spawn_time[parent_conversation_id] = now

        child = self.repo.create(
            parent_conversation_id, parent_turn_id, name, task, depth, model_profile,
            paths, tools, token_budget, timeout_seconds,
            capabilities=effective_caps,
        )
        self.repo.update(child.id, state="QUEUED", started_at=time.time())
        self._on_event("ChildAgentSpawned", {"child_id": child.id, "name": name, "task": task})
        self._pool.submit(self._run_child, child.id, task, ws_id, timeout_seconds, worker)
        return self.repo.get(child.id)

    def _child_security_context(self, child) -> ExecutionSecurityContext:
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
        )

    def _run_child(self, child_id, task, workspace_id, timeout_seconds, worker=None):
        self.repo.update(child_id, state="RUNNING", task_started_at=time.time())
        self._on_event("ChildAgentProgress", {
            "child_id": child_id, "status": "RUNNING",
            "summary": task[:80], "progress": 10,
        })
        try:
            result = worker(task) if worker is not None else self._execute_worker(child_id, task, timeout_seconds)
            child = self.repo.get(child_id)
            if not child or child.state == "CANCELLED":
                return
            artifact = self.artifacts.put(
                workspace_id, result, "CHILD_RESULT",
                f"Result from retained child {child_id}",
                child.parent_conversation_id, child.parent_turn_id,
                metadata={"child_session_id": child_id, "runtime_conversation_id": child.runtime_conversation_id},
            )
            self.repo.update(
                child_id, state="COMPLETED", result_artifact_id=artifact.id,
                completed_at=time.time(), context_summary=str(result)[-4000:],
            )
            self._on_event("ChildAgentFinished", {"child_id": child_id, "status": "COMPLETED", "error": None})
        except Exception as exc:
            child = self.repo.get(child_id)
            if child and child.state == "CANCELLED":
                return
            state = "TIMED_OUT" if isinstance(exc, TimeoutError) else "FAILED"
            self.repo.update(child_id, state=state, error=str(exc), completed_at=time.time())
            self._on_event("ChildAgentFinished", {"child_id": child_id, "status": state, "error": str(exc)})
        finally:
            with self._execution_lock:
                self._processes.pop(child_id, None)

    def _execute_worker(self, child_id, task, timeout_seconds):
        child = self.repo.get(child_id)
        if not child:
            raise ValueError("Child not found")
        sec = self._child_security_context(child)
        payload = {
            "root": str(self.root),
            "child_id": child.id,
            "runtime_conversation_id": child.runtime_conversation_id or f"childconv_{child.id}",
            "task": task,
            "allowed_paths": child.allowed_paths,
            "security_context": sec.to_dict(),
        }
        proc = subprocess.Popen(
            [sys.executable, "-m", "kitt.children.worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=str(self.root), start_new_session=(sys.platform != "win32"),
        )
        with self._execution_lock:
            self._processes[child_id] = proc
        try:
            out, err = proc.communicate(json.dumps(payload), timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            self._kill_tree(proc)
            raise TimeoutError("child timed out")
        if proc.returncode != 0:
            raise RuntimeError(f"child worker exited {proc.returncode}: {err[-2000:]}")
        marker = "KITT_CHILD_RESULT:"
        line = next((ln for ln in reversed(out.splitlines()) if ln.startswith(marker)), None)
        if not line:
            raise RuntimeError("child worker returned no structured result")
        data = json.loads(line[len(marker):])
        if not data.get("success"):
            raise RuntimeError(data.get("error", "child worker failed"))
        self.repo.update(child_id, tokens_used=int(data.get("tokens_used", 0)))
        return str(data.get("output", ""))

    @staticmethod
    def _kill_tree(proc):
        if proc.poll() is not None:
            return
        try:
            if sys.platform != "win32":
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=1.5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
            else:
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
                )
        finally:
            try:
                proc.kill()
            except Exception:
                pass

    def inspect(self, child_id, conversation_id=None, workspace_id=None):
        return self.repo.get_scoped(child_id, conversation_id, workspace_id)

    def list(self, parent_conversation_id, limit=20):
        return self.repo.list(parent_conversation_id, limit)

    def cancel(self, child_id, conversation_id=None, workspace_id=None):
        child = self.inspect(child_id, conversation_id, workspace_id)
        if not child or child.state in {"COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED"}:
            return False
        self.repo.update(child_id, state="CANCELLED", error="cancelled by user", completed_at=time.time())
        with self._execution_lock:
            proc = self._processes.get(child_id)
        if proc:
            self._kill_tree(proc)
        return True

    def wait(self, child_id, timeout=60.0):
        deadline = time.monotonic() + max(0.1, timeout)
        while time.monotonic() < deadline:
            child = self.repo.get(child_id)
            if child and child.state in {"COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED", "WAITING_APPROVAL"}:
                return child
            time.sleep(0.02)
        return self.repo.get(child_id)

    def retain(self, child_id, conversation_id=None, workspace_id=None):
        child = self.inspect(child_id, conversation_id, workspace_id)
        if not child:
            return False
        self.repo.update(child_id, state="RETAINED")
        self._on_event("ChildAgentRetained", {"child_id": child_id, "name": child.name})
        return True

    def assign_task(self, child_id, task, workspace_id=None, timeout_seconds=120.0,
                    worker=None, conversation_id=None):
        child = self.inspect(child_id, conversation_id, workspace_id or self.workspace_id)
        if not child:
            raise ValueError("Child not found")
        if child.state not in {"RETAINED", "COMPLETED", "IDLE"}:
            raise ValueError(f"Cannot assign task in state {child.state}")
        self.repo.update(
            child_id, state="QUEUED", task=task, error=None,
            started_at=time.time(), completed_at=None,
            current_task_id=f"task_{uuid.uuid4().hex}", task_started_at=time.time(),
        )
        self._pool.submit(self._run_child, child_id, task, workspace_id or self.workspace_id, timeout_seconds, worker)
        return self.repo.get(child_id)

    def send_message(self, conversation_id, parent_id, child_id, sender_id, recipient_id,
                     payload, kind="DIRECT", correlation_id=None, reply_to=None, trace_id=None):
        self.repo.get_scoped(child_id, conversation_id, self.workspace_id)
        if sender_id != parent_id and recipient_id != parent_id:
            if not self.allow_peer_agent_messages:
                raise PermissionError("Peer agent messages disabled")
            s = self.repo.get_scoped(sender_id, conversation_id, self.workspace_id)
            r = self.repo.get_scoped(recipient_id, conversation_id, self.workspace_id)
            if not s or not r:
                raise PermissionError("Peer child scope mismatch")
        msg = self.messaging.send(
            conversation_id=conversation_id, parent_id=parent_id, child_id=child_id,
            sender_id=sender_id, recipient_id=recipient_id, payload=payload, kind=kind,
            correlation_id=correlation_id, reply_to=reply_to, trace_id=trace_id,
        )
        self._on_event("ChildAgentMessageSent", {"message_id": msg.id, "kind": kind, "correlation_id": correlation_id})
        return msg

    def list_messages(self, conversation_id, child_id=None, limit=50):
        if child_id:
            self.repo.get_scoped(child_id, conversation_id, self.workspace_id)
        return self.messaging.list_messages(conversation_id, child_id=child_id, limit=limit)

    def ask(self, child_id, question, timeout=30.0):
        child = self.repo.get_scoped(child_id, workspace_id=self.workspace_id)
        if not child:
            raise ValueError("Child not found")
        corr = f"corr_{uuid.uuid4().hex}"
        original = self.send_message(
            child.parent_conversation_id, child.parent_conversation_id, child.id,
            child.parent_conversation_id, child.id, {"question": question},
            kind="ASK", correlation_id=corr,
        )

        def answer():
            try:
                result = self._execute_worker(
                    child.id,
                    f"Parent asks a retained specialist question. Answer directly using retained context:\n{question}",
                    min(timeout, child.timeout_seconds),
                )
                self.reply(child.id, original, {"answer": result})
            except Exception as exc:
                self.reply(child.id, original, {"error": str(exc)})

        self._pool.submit(answer)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            replies = self.messaging.list_replies(corr)
            if replies:
                return {"status": "ANSWERED", "reply": replies[0].payload, "correlation_id": corr}
            time.sleep(0.05)
        return {"status": "SENT", "correlation_id": corr, "message_id": original.id}

    def reply(self, child_id, original_message: ChildMessage, payload):
        child = self.repo.get_scoped(child_id, original_message.conversation_id, self.workspace_id)
        return self.send_message(
            child.parent_conversation_id, child.parent_conversation_id, child.id,
            child.id, original_message.sender_id, payload, kind="REPLY",
            correlation_id=original_message.correlation_id, reply_to=original_message.id,
        )

    def broadcast(self, parent_conversation_id, payload):
        return [
            self.send_message(
                parent_conversation_id, parent_conversation_id, c.id,
                parent_conversation_id, c.id, payload, kind="BROADCAST"
            )
            for c in self.list(parent_conversation_id, 50)
            if c.state in {"RUNNING", "QUEUED", "RETAINED", "IDLE"}
        ]

    def close(self):
        with self._execution_lock:
            procs = list(self._processes.values())
        for proc in procs:
            self._kill_tree(proc)
        self._pool.shutdown(wait=False, cancel_futures=True)
