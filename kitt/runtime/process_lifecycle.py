from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kitt.history.redaction import redact
from kitt.security.authority_snapshot import (
    capture_authority_snapshot,
    validate_authority_snapshot,
)
from kitt.tools.process_runner import ProcessRunner, sanitized_subprocess_env


_MAX_STDIN_BYTES = 64 * 1024
_DEFAULT_EVENT_BYTES = 256 * 1024


@dataclass
class _ManagedProcess:
    process_id: str
    process: subprocess.Popen
    plan: Any
    conversation_id: str
    origin_turn_id: str
    authority_snapshot: dict[str, Any]
    started_at: float
    executable: str
    events: deque[dict[str, Any]]
    event_bytes: int = 0
    sequence: int = 0
    exit_recorded: bool = False
    cleanup_done: bool = False


class ManagedProcessManager:
    """Workspace-scoped lifecycle for bounded background processes."""

    def __init__(
        self,
        runner: ProcessRunner,
        registry: Any,
        *,
        workspace_id: str,
        ledger: Any = None,
        max_event_bytes: int = _DEFAULT_EVENT_BYTES,
    ) -> None:
        self.runner = runner
        self.registry = registry
        self.workspace_id = str(workspace_id or "")
        self.ledger = ledger
        self.max_event_bytes = max(4096, int(max_event_bytes))
        self._lock = threading.RLock()
        self._sessions: dict[str, _ManagedProcess] = {}
        self._closed = False

    def _ledger(self):
        if self.ledger is not None:
            return self.ledger
        processor = getattr(self.registry, "_processor", None)
        return getattr(processor, "event_ledger", None)

    def _append_event(
        self,
        session: _ManagedProcess,
        event_type: str,
        payload: dict[str, Any],
        *,
        turn_id: str | None = None,
    ) -> None:
        ledger = self._ledger()
        if ledger is None:
            return
        ledger.append_event(
            session.conversation_id,
            event_type,
            payload,
            turn_id=turn_id or session.origin_turn_id,
            source="managed-process",
            durability="DURABLE",
            replayable=True,
        )

    def _buffer_event(
        self,
        session: _ManagedProcess,
        *,
        stream: str,
        content: str,
    ) -> None:
        safe = redact(str(content or ""))
        if not safe:
            return
        encoded = safe.encode("utf-8", errors="replace")
        with self._lock:
            session.sequence += 1
            item = {
                "seq": session.sequence,
                "stream": stream,
                "content": safe,
                "created_at": time.time(),
            }
            # Persist before exposing the Observation through process.read.
            self._append_event(
                session,
                "PROCESS_OUTPUT",
                {
                    "process_id": session.process_id,
                    "seq": session.sequence,
                    "stream": stream,
                    "content": safe,
                },
            )
            session.events.append(item)
            session.event_bytes += len(encoded)
            while session.events and session.event_bytes > self.max_event_bytes:
                old = session.events.popleft()
                session.event_bytes = max(
                    0,
                    session.event_bytes
                    - len(
                        str(old.get("content") or "").encode(
                            "utf-8",
                            errors="replace",
                        )
                    ),
                )

    def _reader(
        self,
        session: _ManagedProcess,
        pipe,
        stream: str,
    ) -> None:
        try:
            while True:
                chunk = pipe.read(4096)
                if not chunk:
                    break
                self._buffer_event(
                    session,
                    stream=stream,
                    content=chunk.decode("utf-8", errors="replace"),
                )
        finally:
            try:
                pipe.close()
            except Exception:
                pass

    def _cleanup_plan(self, session: _ManagedProcess) -> None:
        with self._lock:
            if session.cleanup_done:
                return
            session.cleanup_done = True
        try:
            self.runner.sandbox.cleanup(session.plan)
        except Exception:
            pass

    def _waiter(self, session: _ManagedProcess) -> None:
        returncode = session.process.wait()
        with self._lock:
            if session.exit_recorded:
                self._cleanup_plan(session)
                return
            session.exit_recorded = True
        self._append_event(
            session,
            "PROCESS_EXIT",
            {
                "process_id": session.process_id,
                "returncode": int(returncode),
                "duration_ms": max(
                    0.0,
                    (time.monotonic() - session.started_at) * 1000.0,
                ),
            },
        )
        self._cleanup_plan(session)

    def _authority(
        self,
        security_context,
        *,
        sandbox_profile: str,
    ) -> dict[str, Any]:
        policy = getattr(self.registry, "policy", None)
        approval = getattr(self.registry, "approval_manager", None)
        autonomy = getattr(policy, "autonomy", None)
        if policy is None or approval is None or autonomy is None:
            raise PermissionError(
                "managed process requires attached policy and approval authority"
            )
        return capture_authority_snapshot(
            security_context,
            policy=policy,
            autonomy=autonomy,
            approval_manager=approval,
            sandbox_profile=sandbox_profile,
        )

    def _get(self, process_id: str) -> _ManagedProcess:
        with self._lock:
            session = self._sessions.get(str(process_id or ""))
        if session is None:
            raise KeyError(f"managed process not found: {process_id}")
        return session

    def _validate_control(
        self,
        session: _ManagedProcess,
        security_context,
    ) -> None:
        if security_context is None:
            raise PermissionError("ExecutionSecurityContext is required")
        security_context.assert_scope(
            self.workspace_id,
            session.conversation_id,
        )
        snapshot = session.authority_snapshot.get("snapshot") or {}
        original_turn = str(snapshot.get("turn_id") or session.origin_turn_id)
        original_context = security_context.with_turn(original_turn)
        validate_authority_snapshot(
            session.authority_snapshot,
            original_context,
            policy=self.registry.policy,
            autonomy=self.registry.policy.autonomy,
            approval_manager=self.registry.approval_manager,
            sandbox_profile=str(snapshot.get("sandbox_profile") or "workspace-write"),
        )

    def start(
        self,
        *,
        argv: list[str],
        conversation_id: str,
        turn_id: str,
        security_context,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        sandbox_profile: str = "workspace-write",
        require_strong_sandbox: bool = False,
    ) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError("managed process manager is closed")
        if not argv or not all(isinstance(item, str) and item for item in argv):
            raise ValueError("argv must be a non-empty string list")
        resolved_cwd = self.runner.validate_cwd(cwd)
        plan = self.runner.sandbox.plan(
            argv,
            resolved_cwd,
            profile=sandbox_profile,
            require_strong=require_strong_sandbox,
        )
        authority = self._authority(
            security_context,
            sandbox_profile=plan.profile,
        )
        process_env = sanitized_subprocess_env(env)
        process_env.update(plan.env_overrides)
        kwargs: dict[str, Any] = {
            "cwd": plan.host_cwd,
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": False,
            "shell": False,
            "close_fds": True,
            "restore_signals": True,
            "env": process_env,
        }
        if os.name != "nt":
            kwargs["start_new_session"] = True
        else:
            kwargs["creationflags"] = getattr(
                subprocess,
                "CREATE_NEW_PROCESS_GROUP",
                0,
            )

        try:
            proc = subprocess.Popen(plan.argv, **kwargs)
        except Exception:
            self.runner.sandbox.cleanup(plan)
            raise

        process_id = f"proc_{uuid.uuid4().hex}"
        session = _ManagedProcess(
            process_id=process_id,
            process=proc,
            plan=plan,
            conversation_id=conversation_id,
            origin_turn_id=turn_id,
            authority_snapshot=authority,
            started_at=time.monotonic(),
            executable=Path(str(argv[0])).name,
            events=deque(),
        )
        with self._lock:
            self._sessions[process_id] = session

        self._append_event(
            session,
            "PROCESS_STARTED",
            {
                "process_id": process_id,
                "executable": session.executable,
                "sandbox_profile": plan.profile,
                "sandbox_backend": plan.backend,
                "sandbox_strong": bool(plan.strong),
                "network_isolated": bool(plan.network_isolated),
            },
        )
        for pipe, stream in (
            (proc.stdout, "stdout"),
            (proc.stderr, "stderr"),
        ):
            threading.Thread(
                target=self._reader,
                args=(session, pipe, stream),
                name=f"kitt-{process_id}-{stream}",
                daemon=True,
            ).start()
        threading.Thread(
            target=self._waiter,
            args=(session,),
            name=f"kitt-{process_id}-wait",
            daemon=True,
        ).start()

        return {
            "process_id": process_id,
            "pid": proc.pid,
            "running": True,
            "sandbox_profile": plan.profile,
            "sandbox_backend": plan.backend,
            "sandbox_strong": bool(plan.strong),
            "network_isolated": bool(plan.network_isolated),
        }

    def read(
        self,
        process_id: str,
        *,
        security_context,
        after_seq: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        session = self._get(process_id)
        security_context.assert_scope(
            self.workspace_id,
            session.conversation_id,
        )
        bounded = max(1, min(int(limit), 500))
        cursor = max(0, int(after_seq))
        with self._lock:
            events = [
                dict(item)
                for item in session.events
                if int(item.get("seq") or 0) > cursor
            ][:bounded]
            latest = session.sequence
        return {
            "process_id": process_id,
            "running": session.process.poll() is None,
            "returncode": session.process.poll(),
            "events": events,
            "next_seq": latest,
        }

    def stdin(
        self,
        process_id: str,
        data: str,
        *,
        security_context,
        turn_id: str,
    ) -> dict[str, Any]:
        session = self._get(process_id)
        self._validate_control(session, security_context)
        raw = str(data or "").encode("utf-8")
        if len(raw) > _MAX_STDIN_BYTES:
            raise ValueError("managed process stdin exceeds 64 KiB")
        if session.process.poll() is not None or session.process.stdin is None:
            raise RuntimeError("managed process is not accepting stdin")
        session.process.stdin.write(raw)
        session.process.stdin.flush()
        self._append_event(
            session,
            "PROCESS_STDIN",
            {
                "process_id": process_id,
                "bytes_count": len(raw),
            },
            turn_id=turn_id,
        )
        return {"process_id": process_id, "bytes_written": len(raw)}

    def signal(
        self,
        process_id: str,
        signal_name: str,
        *,
        security_context,
        turn_id: str,
    ) -> dict[str, Any]:
        session = self._get(process_id)
        self._validate_control(session, security_context)
        name = str(signal_name or "").strip().upper()
        allowed = {
            "INT": signal.SIGINT,
            "TERM": signal.SIGTERM,
        }
        if hasattr(signal, "SIGSTOP"):
            allowed["STOP"] = signal.SIGSTOP
        if hasattr(signal, "SIGCONT"):
            allowed["CONT"] = signal.SIGCONT
        if name not in allowed:
            raise ValueError(
                "signal must be one of " + ", ".join(sorted(allowed))
            )
        if session.process.poll() is not None:
            raise RuntimeError("managed process already exited")
        sig = allowed[name]
        if os.name != "nt":
            os.killpg(session.process.pid, sig)
        else:
            session.process.send_signal(sig)
        self._append_event(
            session,
            "PROCESS_SIGNAL",
            {"process_id": process_id, "signal": name},
            turn_id=turn_id,
        )
        return {"process_id": process_id, "signal": name}

    def resume(
        self,
        process_id: str,
        *,
        security_context,
        turn_id: str,
    ) -> dict[str, Any]:
        if not hasattr(signal, "SIGCONT"):
            raise RuntimeError("process resume is not supported on this platform")
        return self.signal(
            process_id,
            "CONT",
            security_context=security_context,
            turn_id=turn_id,
        )

    def stop(
        self,
        process_id: str,
        *,
        security_context,
        turn_id: str,
    ) -> dict[str, Any]:
        session = self._get(process_id)
        self._validate_control(session, security_context)
        if session.process.poll() is None:
            self._append_event(
                session,
                "PROCESS_STOP_REQUESTED",
                {"process_id": process_id},
                turn_id=turn_id,
            )
            ProcessRunner._terminate_tree(session.process)
        return {
            "process_id": process_id,
            "running": session.process.poll() is None,
            "returncode": session.process.poll(),
        }

    def close(self) -> None:
        self._closed = True
        with self._lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            if session.process.poll() is None:
                ProcessRunner._terminate_tree(session.process)
            self._cleanup_plan(session)


__all__ = ["ManagedProcessManager"]
