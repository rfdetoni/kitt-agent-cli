from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
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
        self._managed_control_refreshed = False
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
        decision_mode: str | None = None,
        decision_model: str | None = None,
        decision_url: str | None = None,
        decision_timeout_ms: int | None = None,
        temporary_chat: str | None = None,
    ) -> ReverseProxyInstance:
        self._refresh_managed_control_plane()
        args = ["service", "start", target]
        if profile:
            args.extend(["--profile", profile])
        if instance_id:
            args.extend(["--id", instance_id])
        if port is not None:
            args.extend(["--port", str(port)])
        model_params = self._model_settings(
            decision_mode=decision_mode,
            decision_model=decision_model,
            decision_url=decision_url,
            decision_timeout_ms=decision_timeout_ms,
            temporary_chat=temporary_chat,
        )
        for flag, key in (
            ("--decision-mode", "decision_mode"),
            ("--decision-model", "decision_model"),
            ("--decision-url", "decision_url"),
            ("--decision-timeout-ms", "decision_timeout_ms"),
            ("--temporary-chat", "temporary_chat"),
        ):
            if key in model_params:
                args.extend([flag, str(model_params[key])])

        try:
            log_level = int((os.getenv("KITT_LOG_LEVEL", "0") or "0").strip() or "0")
        except ValueError:
            log_level = 0
        log_level = max(0, min(2, log_level))
        log_content = (os.getenv("KITT_LOG_CONTENT", "") or "").strip().lower()
        if log_content not in {"none", "metadata", "full"}:
            log_content = "metadata"  # full prompts/responses require explicit opt-in
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
                **model_params,
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
        if log_file:
            expected_dir = Path(log_file).expanduser().resolve().parent
            actual_log = (
                Path(parsed.log_file).expanduser().resolve()
                if parsed.log_file
                else None
            )
            if actual_log is None or actual_log.parent != expected_dir:
                try:
                    self.stop_instance(parsed.id)
                except Exception:
                    pass
                raise ReverseProxyControlError(
                    "Managed reverse proxy did not honor the Agent log directory. "
                    "Restart KITT after updating kitt-reverse-proxy."
                )
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

    @staticmethod
    def _model_settings(
        *,
        decision_mode: str | None = None,
        decision_model: str | None = None,
        decision_url: str | None = None,
        decision_timeout_ms: int | None = None,
        temporary_chat: str | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if decision_mode is not None:
            if decision_mode not in {"off", "shadow", "assist"}:
                raise ValueError("decision_mode must be off, shadow or assist")
            result["decision_mode"] = decision_mode
        if temporary_chat is not None:
            if temporary_chat not in {"off", "supported", "required"}:
                raise ValueError("temporary_chat must be off, supported or required")
            result["temporary_chat"] = temporary_chat
        if decision_model is not None:
            import re
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}", decision_model):
                raise ValueError("decision_model must be a valid model identifier")
            result["decision_model"] = decision_model
        if decision_url is not None:
            parts = urlsplit(decision_url)
            if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password or parts.path not in {"", "/"} or parts.query or parts.fragment:
                raise ValueError("decision_url must be an HTTP(S) origin without credentials, path or query")
            result["decision_url"] = decision_url.rstrip("/")
        if decision_timeout_ms is not None:
            if type(decision_timeout_ms) is not int or not 100 <= decision_timeout_ms <= 30000:
                raise ValueError("decision_timeout_ms must be between 100 and 30000")
            result["decision_timeout_ms"] = decision_timeout_ms
        return result

    def configure_instance(
        self,
        instance_id: str,
        *,
        decision_mode: str | None = None,
        decision_model: str | None = None,
        decision_url: str | None = None,
        decision_timeout_ms: int | None = None,
        temporary_chat: str | None = None,
    ) -> ReverseProxyInstance:
        settings = self._model_settings(
            decision_mode=decision_mode,
            decision_model=decision_model,
            decision_url=decision_url,
            decision_timeout_ms=decision_timeout_ms,
            temporary_chat=temporary_chat,
        )
        if not settings:
            raise ValueError("No decision-model or temporary-chat setting was provided")
        self._refresh_managed_control_plane()
        args = ["service", "configure", instance_id]
        for flag, key in (
            ("--decision-mode", "decision_mode"),
            ("--decision-model", "decision_model"),
            ("--decision-url", "decision_url"),
            ("--decision-timeout-ms", "decision_timeout_ms"),
            ("--temporary-chat", "temporary_chat"),
        ):
            if key in settings:
                args.extend([flag, str(settings[key])])
        payload = self._request("service.configure", {"id": instance_id, **settings}, *args)
        instance = payload.get("instance")
        if not isinstance(instance, dict):
            raise ReverseProxyControlError("Reverse proxy did not confirm its configuration")
        return ReverseProxyInstance.from_dict(instance)

    def list_decision_models(self, url: str = "http://127.0.0.1:11435") -> list[str]:
        """Read installed Ollaya models; never auto-pull or transmit conversation data."""
        validated = self._model_settings(decision_url=url)
        endpoint = validated["decision_url"]
        request = urllib.request.Request(endpoint + "/api/tags", method="GET")
        try:
            with urllib.request.urlopen(request, timeout=min(self.timeout_seconds, 4.0)) as response:
                payload = json.loads(response.read(1024 * 1024).decode("utf-8"))
        except (OSError, ValueError, urllib.error.URLError, urllib.error.HTTPError) as error:
            raise ReverseProxyControlError(f"Cannot list decision models from Ollaya: {error}") from error
        models = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(models, list):
            raise ReverseProxyControlError("Ollaya did not return a models list")
        return sorted({
            str(item.get("name") or item.get("model")).strip()
            for item in models if isinstance(item, dict) and
            isinstance(item.get("name") or item.get("model"), str) and
            item.get("name", item.get("model", ""))
        })

    def restart_instance(self, instance_id: str) -> ReverseProxyInstance:
        # Delegate restart to the proxy owner so persisted model settings survive.
        self._refresh_managed_control_plane()
        payload = self._request(
            "service.restart", {"id": instance_id},
            "service", "restart", instance_id,
        )
        instance = payload.get("instance")
        if not isinstance(instance, dict):
            raise ReverseProxyControlError("Reverse proxy did not return the restarted instance")
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

    def _control_server_ready(self) -> bool:
        try:
            request = urllib.request.Request(
                f"{self.control_url}/healthz",
                method="GET",
            )
            with urllib.request.urlopen(
                request,
                timeout=min(self.timeout_seconds, 1.0),
            ) as response:
                return response.status == 200
        except (OSError, ValueError, urllib.error.URLError, urllib.error.HTTPError):
            return False

    def _refresh_managed_control_plane(self) -> None:
        if self._managed_control_refreshed:
            return

        # The resident control plane can outlive a package upgrade. Older
        # revisions accept service.start but silently ignore newer fields such
        # as log_file and owner_pid. Recycle it once before managed start/restart
        # so the process matches the installed reverse-proxy binary.
        try:
            self._call("control", "stop")
        except ReverseProxyControlError:
            pass

        for _attempt in range(40):
            if not self._control_server_ready():
                break
            time.sleep(0.05)

        result = self._call("control", "ensure")
        if not bool(result.get("ready")):
            raise ReverseProxyControlError(
                "Could not start the current reverse proxy control plane."
            )
        self._control_bootstrapped = True
        self._managed_control_refreshed = True

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
            if action in {"service.start", "service.restart", "service.configure"}
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
