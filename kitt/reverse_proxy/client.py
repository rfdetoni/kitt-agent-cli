from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

from kitt.reverse_proxy.contracts import (
    ReverseProxyInstance,
    ReverseProxyPlugin,
    ReverseProxyProfile,
)


class ReverseProxyControlError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReverseProxyCommandResult:
    payload: dict[str, Any]


class ReverseProxyClient:
    """Thin client for the reverse-proxy machine-readable control plane."""

    def __init__(self, executable: str | None = None, *, timeout_seconds: float = 15.0):
        self.executable = executable or shutil.which("kitt-reverse-proxy") or "kitt-reverse-proxy"
        self.timeout_seconds = timeout_seconds

    @property
    def available(self) -> bool:
        return bool(shutil.which(self.executable) or shutil.which("kitt-reverse-proxy"))

    def list_instances(self) -> list[ReverseProxyInstance]:
        payload = self._call("service", "list")
        return [
            ReverseProxyInstance.from_dict(item)
            for item in payload.get("instances", [])
            if isinstance(item, dict)
        ]

    def list_profiles(self) -> list[ReverseProxyProfile]:
        payload = self._call("profiles", "list")
        return [
            ReverseProxyProfile.from_dict(item)
            for item in payload.get("profiles", [])
            if isinstance(item, dict)
        ]

    def list_plugins(self) -> list[ReverseProxyPlugin]:
        payload = self._call("plugins", "list")
        return [
            ReverseProxyPlugin.from_dict(item)
            for item in payload.get("plugins", [])
            if isinstance(item, dict)
        ]

    def start_instance(
        self,
        target: str,
        *,
        profile: str | None = None,
        instance_id: str | None = None,
        port: int | None = None,
    ) -> ReverseProxyInstance:
        args = ["service", "start", target]
        if profile:
            args.extend(["--profile", profile])
        if instance_id:
            args.extend(["--id", instance_id])
        if port is not None:
            args.extend(["--port", str(port)])
        payload = self._call(*args)
        instance = payload.get("instance")
        if not isinstance(instance, dict):
            raise ReverseProxyControlError("Reverse proxy did not return a service instance.")
        return ReverseProxyInstance.from_dict(instance)

    def stop_instance(self, instance_id: str) -> bool:
        return bool(self._call("service", "stop", instance_id).get("stopped"))

    def stop_all(self) -> int:
        return int(self._call("service", "stop", "--all").get("stopped") or 0)

    def restart_instance(self, instance_id: str) -> ReverseProxyInstance:
        payload = self._call("service", "restart", instance_id)
        instance = payload.get("instance")
        if not isinstance(instance, dict):
            raise ReverseProxyControlError("Reverse proxy did not return the restarted instance.")
        return ReverseProxyInstance.from_dict(instance)

    def create_profile(self, name: str, provider: str | None = None) -> ReverseProxyProfile:
        args = ["profiles", "create", name]
        if provider:
            args.extend(["--provider", provider])
        payload = self._call(*args)
        profile = payload.get("profile")
        if not isinstance(profile, dict):
            raise ReverseProxyControlError("Reverse proxy did not return the created profile.")
        return ReverseProxyProfile.from_dict(profile)

    def remove_profile(self, profile_id: str) -> bool:
        return bool(self._call("profiles", "remove", profile_id).get("removed"))

    def _call(self, *args: str) -> dict[str, Any]:
        command = [self.executable, *args, "--json"]
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                shell=False,
            )
        except FileNotFoundError as exc:
            raise ReverseProxyControlError(
                "kitt-reverse-proxy executable was not found. Install/update the reverse proxy first."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ReverseProxyControlError(
                f"Reverse proxy control command timed out after {self.timeout_seconds:g}s."
            ) from exc

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise ReverseProxyControlError(detail or f"Reverse proxy control command failed ({completed.returncode}).")

        lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
        if not lines:
            raise ReverseProxyControlError("Reverse proxy control command returned no JSON.")
        try:
            payload = json.loads(lines[-1])
        except json.JSONDecodeError as exc:
            raise ReverseProxyControlError("Reverse proxy control command returned invalid JSON.") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ReverseProxyControlError("Unsupported reverse proxy control-plane schema.")
        return payload
