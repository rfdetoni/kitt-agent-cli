from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Protocol

from kitt.runtime.state import RuntimeStateStore
from kitt.tools.process_runner import sanitized_subprocess_env
from kitt_protocol import ConversationRuntimeBinding, RuntimeBackend


_SECRET_KEYS = ("token", "secret", "password", "credential", "api_key", "authorization")


def _sanitized_config(config: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in dict(config or {}).items():
        normalized = str(key).strip().lower()
        if any(term in normalized for term in _SECRET_KEYS):
            continue
        if isinstance(value, dict):
            clean[str(key)] = _sanitized_config(value)
        elif isinstance(value, (str, int, float, bool)) or value is None:
            clean[str(key)] = value
    return clean


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _run_host(argv: list[str], *, cwd: Path, timeout: float = 120.0) -> dict[str, Any]:
    process = subprocess.run(
        argv,
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,
        env=sanitized_subprocess_env(),
        timeout=max(1.0, min(float(timeout), 3600.0)),
        check=False,
    )
    return {
        "argv": list(argv),
        "returncode": int(process.returncode),
        "stdout": process.stdout[-262144:],
        "stderr": process.stderr[-262144:],
        "success": process.returncode == 0,
    }


class ConversationRuntimeDriver(Protocol):
    backend: RuntimeBackend

    def provision(self, config: dict[str, Any]) -> str: ...
    def attach(self, runtime_id: str, config: dict[str, Any]) -> None: ...
    def execute(self, runtime_id: str, argv: list[str], config: dict[str, Any]) -> dict[str, Any]: ...
    def pause(self, runtime_id: str, config: dict[str, Any]) -> None: ...
    def resume(self, runtime_id: str, config: dict[str, Any]) -> None: ...
    def health(self, runtime_id: str, config: dict[str, Any]) -> dict[str, Any]: ...
    def snapshot_state(self, runtime_id: str, config: dict[str, Any]) -> dict[str, Any]: ...
    def terminate(self, runtime_id: str, config: dict[str, Any]) -> None: ...


class LocalRuntimeDriver:
    backend = RuntimeBackend.LOCAL

    def __init__(self, workspace_root: str | Path):
        self.root = Path(workspace_root).resolve()

    def provision(self, config: dict[str, Any]) -> str:
        return str(config.get("runtime_id") or f"local_{uuid.uuid4().hex}")

    def attach(self, runtime_id: str, config: dict[str, Any]) -> None:
        del runtime_id, config

    def execute(self, runtime_id: str, argv: list[str], config: dict[str, Any]) -> dict[str, Any]:
        del runtime_id
        cwd = self.root / str(config.get("cwd") or ".")
        cwd = cwd.resolve()
        cwd.relative_to(self.root)
        return _run_host(argv, cwd=cwd, timeout=float(config.get("timeout_seconds", 120)))

    def pause(self, runtime_id: str, config: dict[str, Any]) -> None:
        del runtime_id, config

    def resume(self, runtime_id: str, config: dict[str, Any]) -> None:
        del runtime_id, config

    def health(self, runtime_id: str, config: dict[str, Any]) -> dict[str, Any]:
        del config
        return {"ok": self.root.is_dir(), "runtime_id": runtime_id, "backend": self.backend.value}

    def snapshot_state(self, runtime_id: str, config: dict[str, Any]) -> dict[str, Any]:
        del config
        return {
            "runtime_id": runtime_id,
            "backend": self.backend.value,
            "workspace": str(self.root),
            "exists": self.root.exists(),
        }

    def terminate(self, runtime_id: str, config: dict[str, Any]) -> None:
        del runtime_id, config


class ContainerCliRuntimeDriver:
    def __init__(self, backend: RuntimeBackend, binary: str, workspace_root: str | Path):
        if backend not in {RuntimeBackend.DOCKER, RuntimeBackend.PODMAN}:
            raise ValueError("container driver backend must be DOCKER or PODMAN")
        self.backend = backend
        self.binary = binary
        self.root = Path(workspace_root).resolve()

    def provision(self, config: dict[str, Any]) -> str:
        runtime_id = str(config.get("runtime_id") or f"kitt-{uuid.uuid4().hex[:16]}")
        image = str(config.get("image") or "").strip()
        if not image:
            raise ValueError("container runtime requires image")
        result = _run_host(
            [
                self.binary,
                "run",
                "-d",
                "--name",
                runtime_id,
                "-v",
                f"{self.root}:/workspace",
                "-w",
                "/workspace",
                image,
                "sleep",
                "infinity",
            ],
            cwd=self.root,
            timeout=float(config.get("timeout_seconds", 120)),
        )
        if not result["success"]:
            raise RuntimeError(result["stderr"] or result["stdout"])
        return runtime_id

    def attach(self, runtime_id: str, config: dict[str, Any]) -> None:
        state = self.health(runtime_id, config)
        if not state.get("ok"):
            raise RuntimeError(f"{self.backend.value} runtime not available: {runtime_id}")

    def execute(self, runtime_id: str, argv: list[str], config: dict[str, Any]) -> dict[str, Any]:
        return _run_host(
            [self.binary, "exec", runtime_id, *argv],
            cwd=self.root,
            timeout=float(config.get("timeout_seconds", 120)),
        )

    def pause(self, runtime_id: str, config: dict[str, Any]) -> None:
        result = _run_host([self.binary, "pause", runtime_id], cwd=self.root, timeout=30)
        if not result["success"]:
            raise RuntimeError(result["stderr"] or result["stdout"])

    def resume(self, runtime_id: str, config: dict[str, Any]) -> None:
        result = _run_host([self.binary, "unpause", runtime_id], cwd=self.root, timeout=30)
        if not result["success"]:
            raise RuntimeError(result["stderr"] or result["stdout"])

    def health(self, runtime_id: str, config: dict[str, Any]) -> dict[str, Any]:
        result = _run_host(
            [self.binary, "inspect", "-f", "{{.State.Status}}", runtime_id],
            cwd=self.root,
            timeout=30,
        )
        return {
            "ok": bool(result["success"]) and result["stdout"].strip() in {"running", "paused"},
            "state": result["stdout"].strip(),
            "runtime_id": runtime_id,
            "backend": self.backend.value,
        }

    def snapshot_state(self, runtime_id: str, config: dict[str, Any]) -> dict[str, Any]:
        result = _run_host([self.binary, "inspect", runtime_id], cwd=self.root, timeout=30)
        return {
            "runtime_id": runtime_id,
            "backend": self.backend.value,
            "healthy": bool(result["success"]),
            "state_digest": _digest(result["stdout"]),
        }

    def terminate(self, runtime_id: str, config: dict[str, Any]) -> None:
        result = _run_host([self.binary, "rm", "-f", runtime_id], cwd=self.root, timeout=60)
        if not result["success"] and "No such" not in result["stderr"]:
            raise RuntimeError(result["stderr"] or result["stdout"])


class KubernetesRuntimeDriver:
    backend = RuntimeBackend.KUBERNETES

    def __init__(self, workspace_root: str | Path, binary: str = "kubectl"):
        self.root = Path(workspace_root).resolve()
        self.binary = binary

    def _namespace(self, config: dict[str, Any]) -> str:
        return str(config.get("namespace") or "default")

    def provision(self, config: dict[str, Any]) -> str:
        runtime_id = str(config.get("runtime_id") or f"kitt-{uuid.uuid4().hex[:16]}")
        image = str(config.get("image") or "").strip()
        if not image:
            raise ValueError("kubernetes runtime requires image")
        result = _run_host(
            [
                self.binary,
                "-n",
                self._namespace(config),
                "run",
                runtime_id,
                f"--image={image}",
                "--restart=Never",
                "--command",
                "--",
                "sleep",
                "infinity",
            ],
            cwd=self.root,
            timeout=float(config.get("timeout_seconds", 120)),
        )
        if not result["success"]:
            raise RuntimeError(result["stderr"] or result["stdout"])
        return runtime_id

    def attach(self, runtime_id: str, config: dict[str, Any]) -> None:
        if not self.health(runtime_id, config).get("ok"):
            raise RuntimeError(f"Kubernetes runtime not available: {runtime_id}")

    def execute(self, runtime_id: str, argv: list[str], config: dict[str, Any]) -> dict[str, Any]:
        return _run_host(
            [
                self.binary,
                "-n",
                self._namespace(config),
                "exec",
                runtime_id,
                "--",
                *argv,
            ],
            cwd=self.root,
            timeout=float(config.get("timeout_seconds", 120)),
        )

    def pause(self, runtime_id: str, config: dict[str, Any]) -> None:
        del runtime_id, config

    def resume(self, runtime_id: str, config: dict[str, Any]) -> None:
        del runtime_id, config

    def health(self, runtime_id: str, config: dict[str, Any]) -> dict[str, Any]:
        result = _run_host(
            [
                self.binary,
                "-n",
                self._namespace(config),
                "get",
                "pod",
                runtime_id,
                "-o",
                "jsonpath={.status.phase}",
            ],
            cwd=self.root,
            timeout=30,
        )
        state = result["stdout"].strip()
        return {
            "ok": bool(result["success"]) and state in {"Pending", "Running", "Succeeded"},
            "state": state,
            "runtime_id": runtime_id,
            "backend": self.backend.value,
        }

    def snapshot_state(self, runtime_id: str, config: dict[str, Any]) -> dict[str, Any]:
        result = _run_host(
            [
                self.binary,
                "-n",
                self._namespace(config),
                "get",
                "pod",
                runtime_id,
                "-o",
                "json",
            ],
            cwd=self.root,
            timeout=30,
        )
        return {
            "runtime_id": runtime_id,
            "backend": self.backend.value,
            "healthy": bool(result["success"]),
            "state_digest": _digest(result["stdout"]),
        }

    def terminate(self, runtime_id: str, config: dict[str, Any]) -> None:
        result = _run_host(
            [self.binary, "-n", self._namespace(config), "delete", "pod", runtime_id, "--ignore-not-found=true"],
            cwd=self.root,
            timeout=60,
        )
        if not result["success"]:
            raise RuntimeError(result["stderr"] or result["stdout"])


class RemoteRuntimeDriver:
    backend = RuntimeBackend.REMOTE

    def __init__(self, secret_provider: Callable[[dict[str, Any]], str | None] | None = None):
        self.secret_provider = secret_provider or (lambda config: os.getenv(str(config.get("token_env") or "KITT_REMOTE_RUNTIME_TOKEN")))

    def _request(self, runtime_id: str, action: str, config: dict[str, Any], payload: dict[str, Any] | None = None) -> dict[str, Any]:
        endpoint = str(config.get("endpoint") or "").rstrip("/")
        if not endpoint.startswith(("https://", "http://127.0.0.1:", "http://localhost:")):
            raise PermissionError("remote runtime endpoint must use HTTPS or loopback HTTP")
        token = self.secret_provider(config)
        body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{endpoint}/v1/runtimes/{runtime_id}/{action}",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {token}"} if token else {}),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=float(config.get("timeout_seconds", 120))) as response:
                value = json.loads(response.read(1024 * 1024).decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"remote runtime request failed: {type(exc).__name__}") from exc
        if not isinstance(value, dict):
            raise RuntimeError("remote runtime returned non-object response")
        return value

    def provision(self, config: dict[str, Any]) -> str:
        runtime_id = str(config.get("runtime_id") or f"remote_{uuid.uuid4().hex}")
        result = self._request(runtime_id, "provision", config, {"config": _sanitized_config(config)})
        return str(result.get("runtime_id") or runtime_id)

    def attach(self, runtime_id: str, config: dict[str, Any]) -> None:
        self._request(runtime_id, "attach", config)

    def execute(self, runtime_id: str, argv: list[str], config: dict[str, Any]) -> dict[str, Any]:
        return self._request(runtime_id, "execute", config, {"argv": list(argv)})

    def pause(self, runtime_id: str, config: dict[str, Any]) -> None:
        self._request(runtime_id, "pause", config)

    def resume(self, runtime_id: str, config: dict[str, Any]) -> None:
        self._request(runtime_id, "resume", config)

    def health(self, runtime_id: str, config: dict[str, Any]) -> dict[str, Any]:
        return self._request(runtime_id, "health", config)

    def snapshot_state(self, runtime_id: str, config: dict[str, Any]) -> dict[str, Any]:
        return self._request(runtime_id, "snapshot", config)

    def terminate(self, runtime_id: str, config: dict[str, Any]) -> None:
        self._request(runtime_id, "terminate", config)


class ConversationRuntimeManager:
    STATE_KEY = "conversation.runtime.binding"
    CONFIG_KEY = "conversation.runtime.config"

    def __init__(
        self,
        workspace_root: str | Path,
        workspace_id: str,
        conversation_id: str,
        db,
        *,
        ledger=None,
        drivers: dict[RuntimeBackend, ConversationRuntimeDriver] | None = None,
    ):
        self.root = Path(workspace_root).resolve()
        self.workspace_id = str(workspace_id)
        self.conversation_id = str(conversation_id)
        self.state = RuntimeStateStore(db, self.workspace_id, self.conversation_id)
        self.ledger = ledger
        self.drivers = drivers or {
            RuntimeBackend.LOCAL: LocalRuntimeDriver(self.root),
            RuntimeBackend.DOCKER: ContainerCliRuntimeDriver(RuntimeBackend.DOCKER, "docker", self.root),
            RuntimeBackend.PODMAN: ContainerCliRuntimeDriver(RuntimeBackend.PODMAN, "podman", self.root),
            RuntimeBackend.KUBERNETES: KubernetesRuntimeDriver(self.root),
            RuntimeBackend.REMOTE: RemoteRuntimeDriver(),
        }

    def _event(self, name: str, binding: ConversationRuntimeBinding, extra: dict[str, Any] | None = None) -> None:
        if self.ledger is None:
            return
        self.ledger.append_event(
            self.conversation_id,
            name,
            {
                "conversation_id": binding.conversation_id,
                "backend": binding.backend.value,
                "runtime_id": binding.runtime_id,
                "state": binding.state,
                "config_digest": binding.config_digest,
                **dict(extra or {}),
            },
            source="conversation-runtime",
            durability="DURABLE",
            replayable=True,
        )

    def _save(self, binding: ConversationRuntimeBinding, config: dict[str, Any]) -> ConversationRuntimeBinding:
        self.state.set(self.STATE_KEY, {
            **asdict(binding),
            "backend": binding.backend.value,
        })
        self.state.set(self.CONFIG_KEY, _sanitized_config(config))
        return binding

    def binding(self) -> ConversationRuntimeBinding | None:
        raw = self.state.get(self.STATE_KEY)
        if not isinstance(raw, dict):
            return None
        try:
            return ConversationRuntimeBinding(
                conversation_id=str(raw["conversation_id"]),
                backend=RuntimeBackend(str(raw["backend"])),
                runtime_id=str(raw["runtime_id"]),
                state=str(raw["state"]),
                workspace_ref=str(raw["workspace_ref"]),
                config_digest=str(raw["config_digest"]),
                created_at=int(raw["created_at"]),
                updated_at=int(raw["updated_at"]),
            )
        except Exception:
            return None

    def _config(self) -> dict[str, Any]:
        raw = self.state.get(self.CONFIG_KEY)
        return dict(raw) if isinstance(raw, dict) else {}

    def _driver(self, backend: RuntimeBackend) -> ConversationRuntimeDriver:
        driver = self.drivers.get(backend)
        if driver is None:
            raise RuntimeError(f"runtime backend unavailable: {backend.value}")
        return driver

    def provision(self, backend: RuntimeBackend | str, config: dict[str, Any] | None = None) -> ConversationRuntimeBinding:
        backend = RuntimeBackend(str(getattr(backend, "value", backend)).upper())
        clean = _sanitized_config(dict(config or {}))
        runtime_id = self._driver(backend).provision(dict(config or {}))
        now = int(time.time() * 1000)
        binding = ConversationRuntimeBinding(
            conversation_id=self.conversation_id,
            backend=backend,
            runtime_id=runtime_id,
            state="RUNNING",
            workspace_ref=str(self.root),
            config_digest=_digest(clean),
            created_at=now,
            updated_at=now,
        )
        self._save(binding, clean)
        self._event("ConversationRuntimeProvisioned", binding)
        return binding

    def attach(self, backend: RuntimeBackend | str, runtime_id: str, config: dict[str, Any] | None = None) -> ConversationRuntimeBinding:
        backend = RuntimeBackend(str(getattr(backend, "value", backend)).upper())
        clean = _sanitized_config(dict(config or {}))
        self._driver(backend).attach(str(runtime_id), dict(config or {}))
        now = int(time.time() * 1000)
        binding = ConversationRuntimeBinding(
            conversation_id=self.conversation_id,
            backend=backend,
            runtime_id=str(runtime_id),
            state="RUNNING",
            workspace_ref=str(self.root),
            config_digest=_digest(clean),
            created_at=now,
            updated_at=now,
        )
        self._save(binding, clean)
        self._event("ConversationRuntimeAttached", binding)
        return binding

    def execute(self, argv: list[str]) -> dict[str, Any]:
        binding = self.binding()
        if binding is None:
            raise RuntimeError("conversation has no runtime binding")
        if binding.state != "RUNNING":
            raise RuntimeError(f"conversation runtime is not running: {binding.state}")
        if not argv or not all(isinstance(item, str) and item for item in argv):
            raise ValueError("argv must be a non-empty string list")
        return self._driver(binding.backend).execute(binding.runtime_id, list(argv), self._config())

    def pause(self) -> ConversationRuntimeBinding:
        binding = self.binding()
        if binding is None:
            raise RuntimeError("conversation has no runtime binding")
        if self.ledger is not None:
            self.ledger.durability_fence(
                self.conversation_id,
                turn_id=None,
                reason="conversation-runtime-pause",
            )
        self._driver(binding.backend).pause(binding.runtime_id, self._config())
        updated = ConversationRuntimeBinding(
            **{**asdict(binding), "state": "PAUSED", "updated_at": int(time.time() * 1000)}
        )
        self._save(updated, self._config())
        self._event("ConversationRuntimePaused", updated)
        return updated

    def resume(self) -> ConversationRuntimeBinding:
        binding = self.binding()
        if binding is None:
            raise RuntimeError("conversation has no runtime binding")
        self._driver(binding.backend).resume(binding.runtime_id, self._config())
        updated = ConversationRuntimeBinding(
            **{**asdict(binding), "state": "RUNNING", "updated_at": int(time.time() * 1000)}
        )
        self._save(updated, self._config())
        self._event("ConversationRuntimeResumed", updated)
        return updated

    def health(self) -> dict[str, Any]:
        binding = self.binding()
        if binding is None:
            return {"ok": False, "state": "UNBOUND"}
        return self._driver(binding.backend).health(binding.runtime_id, self._config())

    def snapshot_state(self) -> dict[str, Any]:
        binding = self.binding()
        if binding is None:
            raise RuntimeError("conversation has no runtime binding")
        snapshot = self._driver(binding.backend).snapshot_state(binding.runtime_id, self._config())
        self._event("ConversationRuntimeSnapshot", binding, {"snapshot_digest": _digest(snapshot)})
        return snapshot

    def terminate(self) -> None:
        binding = self.binding()
        if binding is None:
            return
        if self.ledger is not None:
            self.ledger.durability_fence(
                self.conversation_id,
                turn_id=None,
                reason="conversation-runtime-terminate",
            )
        self._driver(binding.backend).terminate(binding.runtime_id, self._config())
        self._event("ConversationRuntimeTerminated", binding)
        self.state.delete(self.STATE_KEY)
        self.state.delete(self.CONFIG_KEY)


class ConversationRuntimeRegistry:
    def __init__(self, workspace_root: str | Path, workspace_id: str, db, *, ledger=None):
        self.root = Path(workspace_root).resolve()
        self.workspace_id = str(workspace_id)
        self.db = db
        self.ledger = ledger

    def for_conversation(self, conversation_id: str) -> ConversationRuntimeManager:
        return ConversationRuntimeManager(
            self.root,
            self.workspace_id,
            conversation_id,
            self.db,
            ledger=self.ledger,
        )


__all__ = [
    "ConversationRuntimeDriver",
    "ConversationRuntimeManager",
    "ConversationRuntimeRegistry",
    "ContainerCliRuntimeDriver",
    "KubernetesRuntimeDriver",
    "LocalRuntimeDriver",
    "RemoteRuntimeDriver",
]
