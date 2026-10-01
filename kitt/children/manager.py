from __future__ import annotations

import threading
from typing import Any

from kitt.children.backends import ChildAgentBackendRegistry
from kitt.children.lifecycle import ChildAgentManager as _LifecycleChildAgentManager
from kitt.core.cancellation import CancellationToken


_DELIVERY_MODES = {"AUTO", "STEER", "FOLLOW_UP"}
_ACTIVE_CHILD_STATES = {"CREATED", "QUEUED", "RUNNING", "WAITING_APPROVAL"}


class ChildAgentManager(_LifecycleChildAgentManager):
    """Retained-agent manager with durable roster and family messaging."""

    def __init__(self, *args, external_backends=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.external_backends = external_backends or ChildAgentBackendRegistry()
        self._external_cancellations: dict[str, CancellationToken] = {}
        self._external_cancellation_lock = threading.RLock()

    def spawn(self, *args, backend=None, **kwargs):
        backend_name = str(backend or "").strip().lower()
        if backend_name:
            if not self.external_backends.enabled:
                raise PermissionError(
                    "External retained agents are disabled. Set KITT_EXTERNAL_AGENTS_ENABLED=1 to opt in."
                )
            status = next(
                (item for item in self.external_backends.statuses() if item.name == backend_name),
                None,
            )
            if status is None:
                raise ValueError(f"Unknown external child backend: {backend_name!r}")
            if not status.available:
                raise FileNotFoundError(f"External child backend '{backend_name}' is not installed")
            kwargs["model_profile"] = f"external:{backend_name}"
        return super().spawn(*args, **kwargs)

    def _execute_worker(self, child_id: str, task: str, timeout_seconds: float) -> dict:
        child = self.repo.get(child_id)
        if not child:
            raise ValueError("Child not found")
        profile = str(child.model_profile or "")
        if not profile.startswith("external:"):
            return super()._execute_worker(child_id, task, timeout_seconds)

        backend_name = profile.split(":", 1)[1].strip()
        if not backend_name:
            raise ValueError("External child is missing backend name")

        payload = self._build_run_payload(child, task)
        token = CancellationToken()
        with self._execution_lock:
            current = self.repo.get(child_id)
            if current is None or current.state in {"CANCELLED", "FAILED", "TIMED_OUT"}:
                token.cancel()
                raise RuntimeError("Child cancelled before external backend admission")
            with self._external_cancellation_lock:
                self._external_cancellations[child_id] = token
        try:
            result = self.external_backends.run(
                backend_name,
                task,
                root_dir=payload["root"],
                timeout_seconds=timeout_seconds,
                cancellation=token,
            )
        finally:
            with self._external_cancellation_lock:
                self._external_cancellations.pop(child_id, None)

        return {
            "success": result.success,
            "state": "COMPLETED" if result.success else "FAILED",
            "output": result.output,
            "error": result.error,
            "tokens_used": 0,
            "backend": result.backend,
            "duration_ms": result.duration_ms,
        }

    def cancel(self, child_id, conversation_id=None, workspace_id=None):
        with self._execution_lock:
            with self._external_cancellation_lock:
                token = self._external_cancellations.get(child_id)
            if token is not None:
                token.cancel()
            return super().cancel(child_id, conversation_id, workspace_id)

    def _resolve_agent(self, conversation_id: str, selector: str):
        value = str(selector or "").strip()
        if not value:
            raise ValueError("agent selector must be non-empty")
        if value in {"parent", conversation_id}:
            return conversation_id, None
        if ":" in value:
            prefix, candidate = value.split(":", 1)
            if prefix.lower() in {"child", "agent", "sibling"}:
                value = candidate.strip()
        children = self.list(conversation_id, 100)
        direct = next((child for child in children if child.id == value), None)
        if direct is None:
            matches = [child for child in children if child.name == value]
            if len(matches) > 1:
                raise ValueError(f"agent selector is ambiguous: {selector!r}")
            direct = matches[0] if matches else None
        if direct is None:
            raise ValueError(f"agent not found in conversation family: {selector!r}")
        return direct.id, direct

    def observe_agents(self, conversation_id: str) -> list[dict[str, Any]]:
        return [
            {
                "id": child.id,
                "name": child.name,
                "state": child.state,
                "task": child.task,
                "model_profile": child.model_profile,
                "tokens_used": child.tokens_used,
                "token_budget": child.token_budget,
                "runtime_conversation_id": child.runtime_conversation_id,
                "result_artifact_id": child.result_artifact_id,
                "error": child.error,
                "last_active_at": child.completed_at or child.started_at or child.created_at,
            }
            for child in self.list(conversation_id, 100)
        ]

    def passivate(self, child_id: str, conversation_id=None, workspace_id=None) -> bool:
        with self._execution_lock:
            child = self.inspect(child_id, conversation_id, workspace_id or self.workspace_id)
            if not child:
                return False
            if child.state in _ACTIVE_CHILD_STATES:
                raise ValueError(f"Cannot passivate active child in state {child.state}")
            if child.state in {"FAILED", "TIMED_OUT", "CANCELLED"}:
                raise ValueError(f"Cannot passivate terminal child in state {child.state}")
            self.repo.update(child_id, state="PASSIVATED")
            self._stop_lease_keeper(child_id)
            if self.coordinator is not None:
                self.coordinator.release_owner(child_id)
            self._on_event("ChildAgentPassivated", {"child_id": child_id, "name": child.name})
            return True

    def revive(
        self,
        child_id: str,
        task: str | None = None,
        *,
        conversation_id=None,
        workspace_id=None,
        timeout_seconds: float | None = None,
    ):
        child = self.inspect(child_id, conversation_id, workspace_id or self.workspace_id)
        if not child:
            raise ValueError("Child not found")
        if child.state != "PASSIVATED":
            raise ValueError(f"Child is not passivated: {child.state}")
        if task and str(task).strip():
            return self.assign_task(
                child_id,
                str(task),
                conversation_id=conversation_id,
                workspace_id=workspace_id or self.workspace_id,
                timeout_seconds=timeout_seconds or child.timeout_seconds,
            )
        self.repo.update(child_id, state="RETAINED")
        revived = self.repo.get(child_id)
        self._on_event("ChildAgentRevived", {"child_id": child_id, "name": child.name})
        return revived

    def send_agent_message(
        self,
        conversation_id: str,
        *,
        sender: str,
        recipient: str,
        message: Any,
        delivery_mode: str = "AUTO",
        correlation_id: str | None = None,
        reply_to: str | None = None,
        trace_id: str | None = None,
    ):
        sender_id, sender_child = self._resolve_agent(conversation_id, sender)
        recipient_id, recipient_child = self._resolve_agent(conversation_id, recipient)
        if sender_id == recipient_id:
            raise ValueError("agent cannot message itself")
        mode = str(delivery_mode or "AUTO").strip().upper()
        if mode not in _DELIVERY_MODES:
            raise ValueError("delivery_mode must be AUTO, STEER, or FOLLOW_UP")
        if mode == "AUTO":
            mode = (
                "STEER"
                if recipient_child is not None and recipient_child.state in _ACTIVE_CHILD_STATES
                else "FOLLOW_UP"
            )
        relation_child = recipient_child or sender_child
        if relation_child is None:
            raise ValueError("message must involve at least one child agent")
        payload = message if isinstance(message, dict) else {"text": str(message)}
        payload = {**payload, "_delivery_mode": mode}
        msg = self.send_message(
            conversation_id=conversation_id,
            parent_id=conversation_id,
            child_id=relation_child.id,
            sender_id=sender_id,
            recipient_id=recipient_id,
            payload=payload,
            kind="AGENT_MESSAGE",
            correlation_id=correlation_id,
            reply_to=reply_to,
            trace_id=trace_id,
        )
        return msg, mode
