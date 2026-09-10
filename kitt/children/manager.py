from __future__ import annotations

import threading

from kitt.children.backends import ChildAgentBackendRegistry
from kitt.children.lifecycle import ChildAgentManager as _LifecycleChildAgentManager
from kitt.core.cancellation import CancellationToken


class ChildAgentManager(_LifecycleChildAgentManager):
    """Retained-agent manager with an optional external backend strategy."""

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
        with self._external_cancellation_lock:
            token = self._external_cancellations.get(child_id)
        if token is not None:
            token.cancel()
        return super().cancel(child_id, conversation_id, workspace_id)
