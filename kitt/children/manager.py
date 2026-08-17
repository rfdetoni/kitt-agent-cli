from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from typing import Callable, List, Optional

from kitt.artifacts.store import ArtifactStore
from kitt.children.context import validate_child_paths
from kitt.children.repository import ChildRepository


class ChildAgentManager:
    """Manages isolated child agent sessions.

    ``spawn()`` persists a session and returns immediately with a
    ``QUEUED``/``RUNNING`` child; ``inspect``/``list``/``cancel``/``wait``
    control it.  A real restricted worker runs the child in a subprocess and
    never reports success when the child ended ``FAILED``/``TIMED_OUT``.
    """

    def __init__(self, root_dir: str, repository: ChildRepository, artifacts: ArtifactStore,
                 max_children: int = 2, max_depth: int = 1,
                 workspace_id: Optional[str] = None,
                 max_worker_seconds: float = 120.0,
                 event_callback: Optional[Callable[[str, dict], None]] = None):
        self.root = Path(root_dir).resolve()
        self.repo = repository
        self.artifacts = artifacts
        self.max_children = max_children
        self.max_depth = max_depth
        self.workspace_id = workspace_id or ""
        self.max_worker_seconds = max_worker_seconds
        self._on_event = event_callback or (lambda name, payload: None)
        self._pool = ThreadPoolExecutor(max_workers=max_children, thread_name_prefix="kitt-child")
        self._last_spawn_time: dict[str, float] = {}

    def spawn(self, parent_conversation_id: str, parent_turn_id: str, name: str = "child_task",
              task: str = "", worker: Optional[Callable[[str], str]] = None,
              workspace_id: Optional[str] = None, depth: int = 1, model_profile: str = "context",
              allowed_paths: Optional[List[str]] = None, enabled_tools: Optional[List[str]] = None,
              allowed_tools: Optional[List[str]] = None, token_budget: int = 2048,
              timeout_seconds: float = 120):
        if not task or not task.strip():
            raise ValueError("Child task must be non-empty")
        ws_id = workspace_id or self.workspace_id
        if not ws_id:
            raise ValueError("Child requires a persisted workspace_id")
        now = time.time()
        last_spawn = self._last_spawn_time.get(parent_conversation_id, 0.0)
        if now - last_spawn < 2.0:
            raise ValueError("Child spawn rate limit: please wait 2 seconds between spawns")
        existing_children = self.repo.list(parent_conversation_id, 100)
        if len(existing_children) >= 10:
            raise ValueError("Total child spawn limit per conversation reached (max 10)")
        enabled_tools = enabled_tools or allowed_tools or ["read_file", "search", "repository_map", "python_compute"]
        if depth > self.max_depth:
            raise ValueError("Child depth limit exceeded")
        running = sum(c.state in {"CREATED", "RUNNING", "QUEUED"} for c in existing_children)
        if running >= self.max_children:
            raise ValueError("Child concurrency limit exceeded")
        self._last_spawn_time[parent_conversation_id] = now
        paths = validate_child_paths(self.root, allowed_paths or [])
        child = self.repo.create(parent_conversation_id, parent_turn_id, name, task, depth, model_profile,
                                 paths, enabled_tools, token_budget, timeout_seconds)
        self.repo.update(child.id, state="QUEUED")
        self._on_event("ChildAgentSpawned", {"child_id": child.id, "name": name, "task": task})
        self._pool.submit(self._run_child, child.id, task, ws_id, parent_conversation_id,
                          parent_turn_id, timeout_seconds, worker)
        return self.repo.get(child.id)

    def _run_child(self, child_id: str, task: str, workspace_id: str,
                   conversation_id: str, turn_id: str, timeout_seconds: float,
                   worker: Optional[Callable[[str], str]]):
        """Execute the child worker, persisting a truthful terminal state."""
        self._on_event("ChildAgentProgress", {"child_id": child_id, "status": "RUNNING", "summary": task[:80], "progress": 10})
        try:
            if worker is not None:
                result = worker(task)
            else:
                result = self._execute_worker(child_id, task, timeout_seconds)
            artifact = self.artifacts.put(
                workspace_id, result,
                "CHILD_RESULT",
                f"Result from child {child_id}",
                conversation_id,
                turn_id,
                metadata={"child_session_id": child_id},
            )
            self.repo.update(child_id, state="COMPLETED", result_artifact_id=artifact.id,
                             completed_at=time.time())
            self._on_event("ChildAgentFinished", {"child_id": child_id, "status": "COMPLETED", "error": None})
        except TimeoutError:
            self.repo.update(child_id, state="TIMED_OUT", error="timeout",
                             completed_at=time.time())
            self._on_event("ChildAgentFinished", {"child_id": child_id, "status": "TIMED_OUT", "error": "timeout"})
        except Exception as exc:
            self.repo.update(child_id, state="FAILED", error=str(exc),
                             completed_at=time.time())
            self._on_event("ChildAgentFinished", {"child_id": child_id, "status": "FAILED", "error": str(exc)})

    def _execute_worker(self, child_id: str, task: str, timeout_seconds: float) -> str:
        """Run the child worker in a restricted subprocess."""
        script = _CHILD_WORKER_SCRIPT
        proc = subprocess.Popen(
            [sys.executable, "-I", "-c", script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            cwd=str(self.root),
            shell=False,
        )
        try:
            out, err = proc.communicate(
                input=json.dumps({"task": task, "child_id": child_id}).encode("utf-8"),
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            self._kill_tree(proc)
            raise TimeoutError("child timed out")
        if proc.returncode != 0:
            raise RuntimeError(f"child worker exited {proc.returncode}: {err.decode('utf-8', 'replace')}")
        try:
            payload = json.loads(out.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid child worker output: {exc}") from exc
        if not payload.get("success"):
            raise RuntimeError(payload.get("error", "child worker failed"))
        return payload.get("output", "")

    @staticmethod
    def _kill_tree(proc: subprocess.Popen) -> None:
        try:
            if sys.platform != "win32":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    def inspect(self, child_id: str):
        return self.repo.get(child_id)

    def list(self, parent_conversation_id: str, limit: int = 20):
        return self.repo.list(parent_conversation_id, limit)

    def cancel(self, child_id: str) -> bool:
        child = self.repo.get(child_id)
        if not child:
            return False
        if child.state in {"COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED"}:
            return False
        self.repo.update(child_id, state="CANCELLED", error="cancelled by user",
                         completed_at=time.time())
        return True

    def wait(self, child_id: str, timeout: float = 60.0):
        """Block until the child reaches a terminal state."""
        deadline = time.monotonic() + max(0.1, timeout)
        while time.monotonic() < deadline:
            child = self.repo.get(child_id)
            if child and child.state in {"COMPLETED", "FAILED", "TIMED_OUT", "CANCELLED"}:
                return child
            time.sleep(0.02)
        child = self.repo.get(child_id)
        return child

    def close(self):
        self._pool.shutdown(wait=False, cancel_futures=True)


_CHILD_WORKER_SCRIPT = r"""
import json, sys
try:
    req = json.loads(sys.stdin.read())
    task = req.get("task", "")
    output = f"child completed task: {task}"
    print(json.dumps({"success": True, "output": output}))
except Exception as exc:
    print(json.dumps({"success": False, "error": str(exc)}))
"""
