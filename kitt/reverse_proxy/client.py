from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
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

    def __init__(
        self,
        executable: str | None = None,
        *,
        timeout_seconds: float = 15.0,
        startup_timeout_seconds: float = 330.0,
        control_url: str | None = None,
    ):
        self.executable = executable or shutil.which("kitt-reverse-proxy") or "kitt-reverse-proxy"
        self.timeout_seconds = timeout_seconds
        self.startup_timeout_seconds = max(timeout_seconds, startup_timeout_seconds)
        port = int(os.environ.get("KITT_REVERSE_PROXY_CONTROL_PORT", "2999"))
        self.control_url = (control_url or f"http://127.0.0.1:{port}").rstrip("/")
        self._control_bootstrapped = False
        self.owner_pid = os.getpid()
        self._owned_instance_ids: set[str] = set()

    @property
    def available(self) -> bool:
        return bool(shutil.which(self.executable) or shutil.which("kitt-reverse-proxy"))

    def list_instances(self) -> list[ReverseProxyInstance]:
        payload = self._request("service.list", {}, "service", "list")
        return [
            ReverseProxyInstance.from_dict(item)
            for item in payload.get("instances", [])
            if isinstance(item, dict)
        ]

    def list_profiles(self) -> list[ReverseProxyProfile]:
        payload = self._request("profiles.list", {}, "profiles", "list")
        return [
            ReverseProxyProfile.from_dict(item)
            for item in payload.get("profiles", [])
            if isinstance(item, dict)
        ]

    def list_plugins(self) -> list[ReverseProxyPlugin]:
        payload = self._request("plugins.list", {}, "plugins", "list")
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

        try:
            log_level = int((os.getenv("KITT_LOG_LEVEL", "0") or "0").strip() or "0")
        except ValueError:
            log_level = 0
        log_level = max(0, min(2, log_level))
        log_content = (os.getenv("KITT_LOG_CONTENT", "") or "").strip().lower()
        if log_content not in {"none", "metadata", "full"}:
            log_content = "full" if log_level >= 2 else "metadata"
        log_file = (
            (os.getenv("KITT_LOG_FILE", "") or "").strip()
            or (os.getenv("KITT_DEBUG_LOG", "") or "").strip()
        )

        args.extend(["--log-level", str(log_level), "--log-content", log_content])
        args.extend(["--owner-pid", str(self.owner_pid)])
        if log_file:
            args.extend(["--log-file", log_file])

        payload = self._request(
            "service.start",
            {
                "target": target,
                **({"profile": profile} if profile else {}),
                **({"id": instance_id} if instance_id else {}),
                **({"port": port} if port is not None else {}),
                "log_level": log_level,
                "log_content": log_content,
                "owner_pid": self.owner_pid,
                **({"log_file": log_file} if log_file else {}),
            },
            *args,
        )
        instance = payload.get("instance")
        if not isinstance(instance, dict):
            raise ReverseProxyControlError("Reverse proxy did not return a service instance.")
        parsed = ReverseProxyInstance.from_dict(instance)
        self._owned_instance_ids.add(parsed.id)
        return parsed

    def stop_instance(self, instance_id: str) -> bool:
        stopped = bool(
            self._request(
                "service.stop",
                {"id": instance_id},
                "service",
                "stop",
                instance_id,
            ).get("stopped")
        )
        if stopped:
            self._owned_instance_ids.discard(instance_id)
        return stopped

    def stop_owned_instances(self) -> int:
        stopped = 0
        for instance_id in list(self._owned_instance_ids):
            try:
                if self.stop_instance(instance_id):
                    stopped += 1
            except Exception:
                # The owner-pid watchdog is the crash-safe fallback.
                continue
        self._owned_instance_ids.clear()
        return stopped

    def stop_all(self) -> int:
        return int(
            self._request(
                "service.stopAll",
                {},
                "service",
                "stop",
                "--all",
            ).get("stopped") or 0
        )

    def restart_instance(self, instance_id: str) -> ReverseProxyInstance:
        payload = self._request(
            "service.restart",
            {"id": instance_id},
            "service",
            "restart",
            instance_id,
        )
        instance = payload.get("instance")
        if not isinstance(instance, dict):
            raise ReverseProxyControlError("Reverse proxy did not return the restarted instance.")
        return ReverseProxyInstance.from_dict(instance)

    def create_profile(self, name: str, provider: str | None = None) -> ReverseProxyProfile:
        args = ["profiles", "create", name]
        if provider:
            args.extend(["--provider", provider])
        payload = self._request(
            "profiles.create",
            {
                "name": name,
                **({"provider": provider} if provider else {}),
            },
            *args,
        )
        profile = payload.get("profile")
        if not isinstance(profile, dict):
            raise ReverseProxyControlError("Reverse proxy did not return the created profile.")
        return ReverseProxyProfile.from_dict(profile)

    def remove_profile(self, profile_id: str) -> bool:
        return bool(
            self._request(
                "profiles.remove",
                {"id": profile_id},
                "profiles",
                "remove",
                profile_id,
            ).get("removed")
        )

    def _request(
        self,
        action: str,
        params: dict[str, Any],
        *fallback_args: str,
    ) -> dict[str, Any]:
        try:
            return self._http_call(action, params)
        except (OSError, ValueError, urllib.error.URLError, urllib.error.HTTPError):
            if not self._control_bootstrapped:
                self._bootstrap_control_plane()
                try:
                    return self._http_call(action, params)
                except (OSError, ValueError, urllib.error.URLError, urllib.error.HTTPError):
                    pass
        return self._call(*fallback_args)

    def _bootstrap_control_plane(self) -> None:
        self._control_bootstrapped = True
        try:
            subprocess.run(
                [self.executable, "control", "ensure", "--json"],
                check=False,
                capture_output=True,
                text=True,
                timeout=min(self.timeout_seconds, 5.0),
                shell=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return

    def _http_call(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps({"action": action, "params": params}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.control_url}/v1/control",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        timeout = (
            self.startup_timeout_seconds
            if action in {"service.start", "service.restart"}
            else min(self.timeout_seconds, 5.0)
        )
        with urllib.request.urlopen(
            request,
            timeout=timeout,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValueError("Unsupported reverse proxy control-plane schema.")
        if payload.get("error"):
            raise ReverseProxyControlError(str(payload["error"]))
        return payload

    def _call(self, *args: str) -> dict[str, Any]:
        command = [self.executable, *args, "--json"]
        startup_command = len(args) >= 2 and args[0] == "service" and args[1] in {"start", "restart"}
        command_timeout = self.startup_timeout_seconds if startup_command else self.timeout_seconds
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=command_timeout,
                shell=False,
            )
        except FileNotFoundError as exc:
            raise ReverseProxyControlError(
                "kitt-reverse-proxy executable was not found. Install/update the reverse proxy first."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ReverseProxyControlError(
                f"Reverse proxy control command timed out after {command_timeout:g}s."
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
