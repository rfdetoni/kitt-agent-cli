from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import urlsplit

from kitt.runtime.state import RuntimeStateStore
from kitt.llm.http_security import secure_urlopen
from kitt.tools.process_runner import ProcessRunner
from kitt_protocol import ConversationRuntimeBinding, RuntimeBackend


_SECRET_KEYS = ("token", "secret", "password", "credential", "api_key", "authorization")


class RuntimeOutcomeUnknown(RuntimeError):
    """An external request may have taken effect; never auto-retry it."""


def _sanitized_config(config: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in dict(config or {}).items():
        normalized = str(key).strip().lower()
        if any(term in normalized for term in _SECRET_KEYS):
            # A variable NAME is a credential reference, not its value.
            # Only retain the identifier, never a literal credential.
            if normalized == "token_env" and isinstance(value, str) and re.fullmatch(
                r"[A-Z_][A-Z0-9_]{0,127}", value
            ):
                clean[str(key)] = value
            continue
        if isinstance(value, dict):
            clean[str(key)] = _sanitized_config(value)
        elif normalized == "auto_backends" and isinstance(value, (tuple, list)):
            clean[str(key)] = [str(getattr(item, "value", item)) for item in value]
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
    # Host-authorized runtime control commands are not agent sandbox commands.
    # Reuse the bounded capture and process-group cleanup without altering their
    # existing authority. Preserve the 256 KiB per-stream capture window.
    runner = ProcessRunner(str(cwd), max_output_bytes=512 * 1024,
                           capture_window_bytes=256 * 1024)
    process = runner.run(argv, timeout_seconds=timeout, sandbox_profile="full-access")
    if process.timed_out:
        raise subprocess.TimeoutExpired(argv, timeout, output=process.stdout, stderr=process.stderr)
    return {
        "argv": list(argv),
        "returncode": int(process.returncode),
        "stdout": process.stdout,
        "stderr": process.stderr,
        "success": process.returncode == 0 and not process.cancelled,
        "truncated": process.truncated,
        "stdout_total_bytes": process.stdout_total_bytes,
        "stderr_total_bytes": process.stderr_total_bytes,
    }


def _runtime_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise ValueError("invalid runtime ID")
    return value


def _remote_endpoint(value: str) -> tuple[str, bool]:
    endpoint = value.rstrip("/")
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError as exc:
        raise PermissionError("invalid remote runtime endpoint") from exc
    del port
    loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if (
        not parsed.hostname or parsed.username is not None or parsed.password is not None
        or parsed.query or parsed.fragment
        or (parsed.scheme != "https" and not (parsed.scheme == "http" and loopback))
        or any(ord(char) < 33 for char in endpoint)
        or "\\" in endpoint
    ):
        raise PermissionError("remote runtime endpoint must use HTTPS or loopback HTTP without credentials")
    return endpoint, loopback


def _readonly_mounts(config: dict[str, Any]) -> list[tuple[str, str]]:
    """Only an explicit host operator can select additional bind sources."""
    mounts = config.get("readonly_mounts", {})
    if not isinstance(mounts, dict) or len(mounts) > 8:
        raise ValueError("readonly_mounts must be an object with at most eight entries")
    result = []
    for alias, source in sorted(mounts.items()):
        if not isinstance(alias, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", alias):
            raise ValueError("invalid readonly mount alias")
        if not isinstance(source, str) or not Path(source).is_absolute():
            raise ValueError("mount source must be an absolute operator-selected directory")
        path = Path(source).resolve(strict=True)
        if not path.is_dir() or path == Path(path.anchor):
            raise ValueError("mount source must be a non-root directory")
        # Docker's comma-delimited mount grammar is not a shell grammar.
        # Reject delimiter injection instead of attempting ambiguous escaping.
        if any(char in str(path) for char in (",", '"', "\n", "\r", "\x00")):
            raise ValueError("mount source contains unsupported delimiters")
        result.append((alias, str(path)))
    return result


def _resource_limits(config: dict[str, Any]) -> tuple[int | None, int | None, int | None]:
    values = []
    for name, minimum, maximum in (
        ("cpu_millis", 10, 256000),
        ("memory_bytes", 16 * 1024 * 1024, 1024 ** 4),
        ("pids_limit", 16, 65536),
    ):
        value = config.get(name)
        if value is not None and (
            type(value) is not int or not minimum <= value <= maximum
        ):
            raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")
        values.append(value)
    return values[0], values[1], values[2]


def _local_capacity() -> tuple[int, int | None]:
    cpu = (os.cpu_count() or 1) * 1000
    try:
        cpu = len(os.sched_getaffinity(0)) * 1000
    except (AttributeError, OSError):
        pass
    available = None
    try:
        available = os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        if available <= 0:
            available = None
    except (AttributeError, OSError, ValueError):
        pass
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()
        if quota != "max":
            cpu = min(cpu, int(quota) * 1000 // int(period))
    except (OSError, ValueError, ZeroDivisionError):
        pass
    try:
        limit = Path("/sys/fs/cgroup/memory.max").read_text().strip()
        if limit != "max":
            free = max(0, int(limit) - int(Path("/sys/fs/cgroup/memory.current").read_text()))
            available = free if available is None else min(available, free)
    except (OSError, ValueError):
        pass
    return cpu, available


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
        runtime_id = _runtime_id(str(config.get("runtime_id") or f"kitt-{uuid.uuid4().hex[:16]}"))
        image = str(config.get("image") or "").strip()
        if not image or image.startswith("-"):
            raise ValueError("container runtime requires a valid image reference")
        cpu, memory, pids = _resource_limits(config)
        flags = []
        if cpu is not None:
            flags += ["--cpus", f"{cpu / 1000:.3f}"]
        if memory is not None:
            flags += ["--memory", str(memory), "--memory-swap", str(memory)]
        if pids is not None:
            flags += ["--pids-limit", str(pids)]
        if config.get("network_disabled") is True:
            flags += ["--network", "none"]
        for alias, source in _readonly_mounts(config):
            flags += ["--mount", f"type=bind,src={source},dst=/mnt/kitt/{alias},readonly"]
        result = _run_host(
            [
                self.binary,
                "run",
                "-d",
                "--name",
                runtime_id,
                *flags,
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
        # An opt-in cold pause frees the container's process memory while
        # preserving its writable layer and mounted workspace.
        action = "stop" if config.get("hibernate_on_pause") is True else "pause"
        result = _run_host([self.binary, action, runtime_id], cwd=self.root, timeout=30)
        if not result["success"]:
            raise RuntimeError(result["stderr"] or result["stdout"])

    def resume(self, runtime_id: str, config: dict[str, Any]) -> None:
        action = "start" if config.get("hibernate_on_pause") is True else "unpause"
        result = _run_host([self.binary, action, runtime_id], cwd=self.root, timeout=30)
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
        runtime_id = _runtime_id(str(config.get("runtime_id") or f"kitt-{uuid.uuid4().hex[:16]}"))
        image = str(config.get("image") or "").strip()
        if not image:
            raise ValueError("kubernetes runtime requires image")
        cpu, memory, pids = _resource_limits(config)
        if pids is not None or config.get("network_disabled") is True:
            raise ValueError("Kubernetes PID/network isolation requires cluster policy")
        limits = {}
        if cpu is not None:
            limits["cpu"] = f"{cpu}m"
        if memory is not None:
            limits["memory"] = str(memory)
        overrides = []
        if limits:
            overrides = [
                "--override-type=strategic",
                "--overrides=" + json.dumps({"spec": {"containers": [{
                    "name": runtime_id,
                    "resources": {"requests": limits, "limits": limits},
                }]}}, separators=(",", ":")),
            ]
        result = _run_host(
            [
                self.binary,
                "-n",
                self._namespace(config),
                "run",
                runtime_id,
                f"--image={image}",
                "--restart=Never",
                *overrides,
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
        endpoint, loopback = _remote_endpoint(str(config.get("endpoint") or ""))
        runtime_id = _runtime_id(runtime_id)
        token = self.secret_provider(config)
        body = json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(body) > 1024 * 1024:
            raise ValueError("remote runtime request exceeds 1 MiB")
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
            timeout = max(1.0, min(float(config.get("timeout_seconds", 120)), 3600.0))
            with secure_urlopen(request, timeout=timeout, max_body_bytes=1024 * 1024,
                                local_only=loopback) as response:
                value = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, UnicodeDecodeError, ValueError) as exc:
            # Size errors contain no remote input; preserve the actionable limit.
            if type(exc) is ValueError and str(exc) == "HTTP response exceeds configured size limit":
                raise RuntimeOutcomeUnknown("remote runtime response exceeds 1 MiB") from exc
            raise RuntimeOutcomeUnknown(f"remote runtime request failed: {type(exc).__name__}") from exc
        if not isinstance(value, dict):
            raise RuntimeOutcomeUnknown("remote runtime returned non-object response")
        return value

    def provision(self, config: dict[str, Any]) -> str:
        runtime_id = str(config.get("runtime_id") or f"remote_{uuid.uuid4().hex}")
        result = self._request(runtime_id, "provision", config, {"config": _sanitized_config(config)})
        return _runtime_id(str(result.get("runtime_id") or runtime_id))

    def attach(self, runtime_id: str, config: dict[str, Any]) -> None:
        self._request(runtime_id, "attach", config)

    def execute(self, runtime_id: str, argv: list[str], config: dict[str, Any]) -> dict[str, Any]:
        return self._request(runtime_id, "execute", config, {"argv": list(argv)})

    def execute_once(self, runtime_id: str, argv: list[str], config: dict[str, Any], request_id: str) -> dict[str, Any]:
        return self._request(runtime_id, "execute", config, {"argv": list(argv), "request_id": request_id})

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


def _exclusive(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with self._operation(method.__name__):
            return method(self, *args, **kwargs)
    return guarded


class ConversationRuntimeManager:
    STATE_KEY = "conversation.runtime.binding"
    CONFIG_KEY = "conversation.runtime.config"
    OPERATION_KEY = "conversation.runtime.operation"
    RECEIPT_PREFIX = "conversation.runtime.receipt."

    @contextmanager
    def _operation(self, name):
        # Cross-process fence; never expire or steal an uncertain operation.
        previous = self.state.get(self.OPERATION_KEY)
        if previous is not None and previous.get("active"):
            raise RuntimeError("runtime operation already active or recovery required")
        claim = {"active": True, "operation": name, "id": uuid.uuid4().hex,
                 "started_at": int(time.time() * 1000)}
        if not self.state.set(self.OPERATION_KEY, claim, expected_value=previous):
            raise RuntimeError("runtime operation already active")
        try:
            yield
        except (RuntimeOutcomeUnknown, TimeoutError, subprocess.TimeoutExpired):
            # The backend may still be operating. Retain the durable fence.
            raise
        except Exception:
            self.state.set(self.OPERATION_KEY, {"active": False}, expected_value=claim)
            raise
        else:
            self.state.set(self.OPERATION_KEY, {"active": False}, expected_value=claim)

    def operation_status(self):
        return self.state.get(self.OPERATION_KEY) or {"active": False}

    def acknowledge_interrupted_operation(self, operation_id: str):
        # Operator must independently confirm the backend is no longer executing.
        previous = self.operation_status()
        if not previous.get("active") or previous.get("id") != operation_id:
            raise ValueError("interrupted operation ID mismatch")
        if not self.state.set(self.OPERATION_KEY, {"active": False}, expected_value=previous):
            raise RuntimeError("runtime operation changed concurrently")


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
        try:
            self.state.set(self.STATE_KEY, {
                **asdict(binding),
                "backend": binding.backend.value,
                "config": _sanitized_config(config),
            })
        except Exception as exc:
            # An external effect may already have occurred. Keep the operation
            # fence rather than inviting a second provision/execute.
            raise RuntimeOutcomeUnknown("runtime binding persistence failed; operator recovery required") from exc
        return binding

    def binding(self) -> ConversationRuntimeBinding | None:
        raw = self.state.get(self.STATE_KEY)
        if not isinstance(raw, dict):
            return None
        try:
            return ConversationRuntimeBinding(
                conversation_id=str(raw["conversation_id"]),
                backend=RuntimeBackend(str(raw["backend"])),
                runtime_id=_runtime_id(str(raw["runtime_id"])),
                state=str(raw["state"]),
                workspace_ref=str(raw["workspace_ref"]),
                config_digest=str(raw["config_digest"]),
                created_at=int(raw["created_at"]),
                updated_at=int(raw["updated_at"]),
            )
        except Exception:
            return None

    def _config(self) -> dict[str, Any]:
        record = self.state.get(self.STATE_KEY)
        if isinstance(record, dict) and isinstance(record.get("config"), dict):
            return dict(record["config"])
        raw = self.state.get(self.CONFIG_KEY)
        return dict(raw) if isinstance(raw, dict) else {}

    def _driver(self, backend: RuntimeBackend) -> ConversationRuntimeDriver:
        driver = self.drivers.get(backend)
        if driver is None:
            raise RuntimeError(f"runtime backend unavailable: {backend.value}")
        return driver

    def _select_auto_backend(self, config: dict[str, Any]) -> RuntimeBackend:
        """Select a configured backend before provision; never weaken isolation.

        The host must supply the ordered allowlist. LOCAL is intentionally not
        eligible for auto selection because it executes directly on the host.
        """
        allowed = config.get("auto_backends")
        if not isinstance(allowed, (tuple, list)) or not allowed:
            raise ValueError("AUTO requires an explicit ordered auto_backends allowlist")
        candidates = [RuntimeBackend(str(getattr(item, "value", item)).upper()) for item in allowed]
        if RuntimeBackend.LOCAL in candidates:
            raise PermissionError("AUTO cannot select the unrestricted LOCAL runtime")
        cpu, memory, pids = _resource_limits(config)
        mounts = _readonly_mounts(config)
        capacity = None
        for backend in candidates:
            driver = self.drivers.get(backend)
            if driver is None:
                continue
            if backend == RuntimeBackend.REMOTE:
                if config.get("endpoint") and not mounts:
                    return backend
            elif backend in {RuntimeBackend.PODMAN, RuntimeBackend.DOCKER, RuntimeBackend.KUBERNETES}:
                binary = getattr(driver, "binary", None)
                if not (config.get("image") and binary and shutil.which(binary)):
                    continue
                if backend == RuntimeBackend.KUBERNETES:
                    if mounts or pids is not None or config.get("network_disabled") is True:
                        continue
                elif cpu is not None or memory is not None:
                    if capacity is None:
                        capacity = _local_capacity()
                    available_cpu, available_memory = capacity
                    if cpu is not None and cpu > available_cpu:
                        continue
                    if memory is not None and (available_memory is None or memory > available_memory):
                        continue
                return backend
        raise RuntimeError("no permitted AUTO backend is available")

    @_exclusive
    def provision(self, backend: RuntimeBackend | str, config: dict[str, Any] | None = None) -> ConversationRuntimeBinding:
        if self.binding() is not None:
            raise RuntimeError("conversation already has a runtime")
        requested = str(getattr(backend, "value", backend)).upper()
        if requested == "AUTO":
            backend = self._select_auto_backend(dict(config or {}))
        else:
            backend = RuntimeBackend(requested)
        # Only one provision call. A failed backend may have produced external
        # effects, so automatic fallback after submission is not safe.
        requested_config = dict(config or {})
        limits = _resource_limits(requested_config)
        if backend == RuntimeBackend.LOCAL and (
            any(value is not None for value in limits) or requested_config.get("network_disabled") is True
        ):
            raise ValueError("LOCAL does not enforce resource or network isolation")
        mounts = _readonly_mounts(requested_config)
        if mounts and backend not in {RuntimeBackend.DOCKER, RuntimeBackend.PODMAN}:
            raise ValueError("readonly mounts require Docker or Podman")
        if mounts:
            requested_config["readonly_mounts"] = dict(mounts)
        clean = _sanitized_config(requested_config)
        runtime_id = self._driver(backend).provision(requested_config)
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

    @_exclusive
    def attach(self, backend: RuntimeBackend | str, runtime_id: str, config: dict[str, Any] | None = None) -> ConversationRuntimeBinding:
        backend = RuntimeBackend(str(getattr(backend, "value", backend)).upper())
        clean = _sanitized_config(dict(config or {}))
        runtime_id = _runtime_id(str(runtime_id))
        existing = self.binding()
        if existing is not None:
            if existing.backend != backend or existing.runtime_id != runtime_id:
                raise RuntimeError("conversation already has a different runtime")
            persisted = self._config()
            # An idempotent reconnect may omit config or repeat existing values,
            # but cannot replace the endpoint, image, limits or credential reference.
            if any(key not in persisted or persisted[key] != value for key, value in clean.items()):
                raise ValueError("reconnect cannot change persisted runtime config")
            if not self.health().get("ok"):
                raise RuntimeError("existing runtime is unavailable")
            return existing
        self._driver(backend).attach(runtime_id, dict(config or {}))
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

    def agent_status(self) -> dict[str, Any]:
        binding = self.binding()
        config = self._config()
        enabled = (binding is not None and binding.backend == RuntimeBackend.REMOTE
                   and config.get("agent_execute") is True
                   and config.get("network_disabled") is True
                   and isinstance(config.get("token_env"), str)
                   and config["token_env"].startswith("KITT_REMOTE_RUNTIME_TOKEN"))
        if not enabled:
            return {"enabled": False}
        return {"enabled": True, "state": binding.state,
                "binding": {"runtime_id": binding.runtime_id,
                            "config_digest": binding.config_digest,
                            "created_at": binding.created_at}}

    @_exclusive
    def execute(self, argv: list[str], *, request_id: str | None = None,
                expected_agent_binding: dict[str, Any] | None = None) -> dict[str, Any]:
        if expected_agent_binding is not None:
            status = self.agent_status()
            if not status["enabled"] or status["binding"] != expected_agent_binding:
                raise PermissionError("approved computer binding changed")
        binding = self.binding()
        if binding is None:
            raise RuntimeError("conversation has no runtime binding")
        if not argv or not all(isinstance(item, str) and item for item in argv):
            raise ValueError("argv must be a non-empty string list")
        config = self._config()
        key = None
        claim = None
        if request_id is not None:
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id):
                raise ValueError("invalid execution request ID")
            key = self.RECEIPT_PREFIX + request_id
            digest = _digest([binding.runtime_id, argv, binding.config_digest])
            previous = self.state.get(key)
            if previous is not None:
                if previous.get("digest") != digest:
                    raise ValueError("execution request ID already used with different arguments")
                return {"receipt": previous, "replayed": True}
        if binding.state == "PAUSED" and config.get("auto_resume") is True:
            binding = self._resume(binding, config)
        if binding.state != "RUNNING":
            raise RuntimeError(f"conversation runtime is not running: {binding.state}")
        if key is not None:
            claim = {"status": "INDETERMINATE", "digest": digest, "request_id": request_id}
            if not self.state.set(key, claim, expected_value=None):
                raise RuntimeError("execution request already claimed")
        # Persist claim before any external effect. Exceptions never trigger replay.
        try:
            driver = self._driver(binding.backend)
            if request_id is not None and isinstance(driver, RemoteRuntimeDriver):
                # Scope the remote receipt to this binding and conversation, never
                # expose an unrelated caller's bare request identifier.
                remote_request_id = _digest([self.workspace_id, self.conversation_id, binding.runtime_id, request_id])
                result = driver.execute_once(binding.runtime_id, list(argv), config, remote_request_id)
            else:
                result = driver.execute(binding.runtime_id, list(argv), config)
            if key is not None:
                receipt = {**claim, "status": "COMPLETED", "result_digest": _digest(result),
                           "success": result.get("success") is True,
                           "returncode": result.get("returncode")}
                self.state.set(key, receipt, expected_value=claim)
                result = {**result, "receipt": receipt, "replayed": False}
            return result
        finally:
            updated = ConversationRuntimeBinding(
                **{**asdict(binding), "updated_at": int(time.time() * 1000)}
            )
            self._save(updated, config)

    @_exclusive
    def hibernate_idle(self, *, now_ms: int | None = None) -> bool:
        binding = self.binding()
        config = self._config()
        seconds = config.get("idle_hibernate_seconds")
        if (binding is None or binding.state != "RUNNING"
                or binding.backend not in {RuntimeBackend.DOCKER, RuntimeBackend.PODMAN}
                or config.get("hibernate_on_pause") is not True
                or type(seconds) is not int or not 30 <= seconds <= 86400):
            return False
        now = int(time.time() * 1000) if now_ms is None else now_ms
        if now - binding.updated_at < seconds * 1000:
            return False
        self._pause(binding, config)
        return True

    @_exclusive
    def pause(self) -> ConversationRuntimeBinding:
        binding = self.binding()
        if binding is None:
            raise RuntimeError("conversation has no runtime binding")
        return self._pause(binding, self._config())

    def _pause(self, binding, config):
        if binding.state == "PAUSED":
            return binding
        if binding.state != "RUNNING":
            raise RuntimeError("runtime is not running")
        if self.ledger is not None:
            self.ledger.durability_fence(
                self.conversation_id,
                turn_id=None,
                reason="conversation-runtime-pause",
            )
        self._driver(binding.backend).pause(binding.runtime_id, config)
        updated = ConversationRuntimeBinding(
            **{**asdict(binding), "state": "PAUSED", "updated_at": int(time.time() * 1000)}
        )
        self._save(updated, config)
        self._event("ConversationRuntimePaused", updated)
        return updated

    @_exclusive
    def resume(self) -> ConversationRuntimeBinding:
        binding = self.binding()
        if binding is None:
            raise RuntimeError("conversation has no runtime binding")
        return self._resume(binding, self._config())

    def _resume(self, binding, config):
        if binding.state == "RUNNING":
            return binding
        if binding.state != "PAUSED":
            raise RuntimeError("runtime is not paused")
        self._driver(binding.backend).resume(binding.runtime_id, config)
        updated = ConversationRuntimeBinding(
            **{**asdict(binding), "state": "RUNNING", "updated_at": int(time.time() * 1000)}
        )
        self._save(updated, config)
        self._event("ConversationRuntimeResumed", updated)
        return updated

    @_exclusive
    def push_artifact(self, store, artifact_id: str, *, allow_sensitive: bool = False):
        from kitt.artifacts.remote_cache import RemoteArtifactCache

        binding = self.binding()
        if binding is None or binding.backend != RuntimeBackend.REMOTE:
            raise ValueError("artifact push requires a bound REMOTE runtime")
        artifact = store.get(artifact_id)
        if (artifact is None or artifact.workspace_id != self.workspace_id
                or artifact.conversation_id != self.conversation_id
                or (artifact.expires_at is not None and artifact.expires_at < time.time()
                    and not artifact.pinned)):
            raise KeyError("artifact unavailable in this conversation")
        if artifact.sensitivity != "NORMAL" and allow_sensitive is not True:
            raise PermissionError("explicit sensitive artifact authorization required")
        driver = self._driver(binding.backend)
        config = self._config()
        cache = RemoteArtifactCache(config, binding.runtime_id, driver.secret_provider(config))
        with store.open_verified(artifact_id) as source:
            transferred = cache.push(source, artifact.content_hash, artifact.size_bytes)
        self._event("ConversationRuntimeArtifactPushed", binding, {
            "artifact_id": artifact_id, "bytes": artifact.size_bytes, "transferred": transferred,
        })
        return {"sha256": artifact.content_hash, "bytes": artifact.size_bytes,
                "transferred": transferred, "remote_path": f"/mnt/kitt/cache/{artifact.content_hash}"}

    @_exclusive
    def pull_artifact(self, store, digest: str, *, summary: str = "Remote runtime artifact"):
        from kitt.artifacts.remote_cache import RemoteArtifactCache

        binding = self.binding()
        if binding is None or binding.backend != RuntimeBackend.REMOTE:
            raise ValueError("artifact pull requires a bound REMOTE runtime")
        driver = self._driver(binding.backend)
        config = self._config()
        cache = RemoteArtifactCache(config, binding.runtime_id, driver.secret_provider(config))
        with cache.open_blob(digest) as response:
            artifact = store.put_stream(
                self.workspace_id, iter(lambda: response.read(64 * 1024), b""),
                "binary", summary[:256], conversation_id=self.conversation_id,
                sensitivity="SENSITIVE", expected_hash=digest,
            )
        self._event("ConversationRuntimeArtifactPulled", binding, {
            "artifact_id": artifact.id, "bytes": artifact.size_bytes,
        })
        return {"artifact_id": artifact.id, "sha256": artifact.content_hash,
                "bytes": artifact.size_bytes}

    def health(self) -> dict[str, Any]:
        binding = self.binding()
        if binding is None:
            return {"ok": False, "state": "UNBOUND"}
        config = self._config()
        health = self._driver(binding.backend).health(binding.runtime_id, config)
        if (
            binding.backend in {RuntimeBackend.DOCKER, RuntimeBackend.PODMAN}
            and binding.state == "PAUSED"
            and config.get("hibernate_on_pause") is True
            and health.get("state") in {"exited", "stopped"}
        ):
            return {**health, "ok": True, "hibernated": True}
        return health

    def snapshot_state(self) -> dict[str, Any]:
        binding = self.binding()
        if binding is None:
            raise RuntimeError("conversation has no runtime binding")
        snapshot = self._driver(binding.backend).snapshot_state(binding.runtime_id, self._config())
        self._event("ConversationRuntimeSnapshot", binding, {"snapshot_digest": _digest(snapshot)})
        return snapshot

    @_exclusive
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

    def hibernate_idle(self) -> int:
        # Only inspect eligible persisted bindings; do not write operation
        # fences for every conversation or contact a backend for inactive ones.
        with self.db.get_connection() as conn:
            rows = conn.execute(
                "SELECT conversation_id FROM runtime_states WHERE workspace_id=? "
                "AND state_key=? AND json_extract(value_json,'$.state')='RUNNING' "
                "AND json_extract(value_json,'$.backend') IN ('DOCKER','PODMAN') "
                "AND json_extract(value_json,'$.config.hibernate_on_pause')=1 "
                "AND json_type(value_json,'$.config.idle_hibernate_seconds')='integer' "
                "AND json_extract(value_json,'$.config.idle_hibernate_seconds') BETWEEN 30 AND 86400 "
                "AND json_extract(value_json,'$.updated_at') + "
                "1000 * json_extract(value_json,'$.config.idle_hibernate_seconds') <= ? "
                "ORDER BY updated_at LIMIT 32",
                (self.workspace_id, ConversationRuntimeManager.STATE_KEY, int(time.time() * 1000)),
            ).fetchall()
        count = 0
        for row in rows:
            try:
                count += self.for_conversation(row[0]).hibernate_idle()
            except (RuntimeError, TimeoutError, subprocess.TimeoutExpired, OSError, ValueError):
                # Busy/uncertain bindings are never stopped by maintenance.
                continue
        return count

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
