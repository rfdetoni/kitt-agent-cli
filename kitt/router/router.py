"""Task router with untrusted workspace config and user-owned credentials."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Dict, Tuple

from kitt.domain.entities import ModelProfile, RouterConfig, TaskStep, TaskType
from kitt.llm.auth import ProviderAuthService
from kitt.router.classifier import TaskClassifier
from kitt.security.workspace_fs import WorkspaceFileSystem

DEFAULT_ROUTER_CONFIG = RouterConfig(
    profiles={
        "context": ModelProfile(backend="ollama", model="qwen2.5:7b-instruct"),
        "execute": ModelProfile(backend="ollama", model="qwen2.5:32b-instruct"),
    },
    routing={
        "context-gather": "context",
        "summarize": "context",
        "code-generation": "execute",
        "code-edit": "execute",
        "validate-diff": "context",
    },
)

_CREDENTIAL_REFERENCE_PREFIXES = ("env:", "auth:", "session:")
_MAX_ROUTER_BYTES = 1024 * 1024
_MAX_PROFILES = 64
_MAX_CUSTOM_PROVIDERS = 64
_MAX_ROUTING = 128
_PROFILE_FIELDS = {
    "backend", "model", "base_url", "api_key", "credential_ref", "protocol",
    "context_window", "max_output_tokens", "temperature", "supports_tools",
    "supports_json", "keep_alive", "request_timeout_seconds",
}


def _safe_ref_for_provider(ref: object, provider: str) -> str | None:
    if not isinstance(ref, str):
        return None
    value = ref.strip()
    if not value.startswith(_CREDENTIAL_REFERENCE_PREFIXES):
        return None
    pid = (provider or "").strip().lower()
    if value.startswith(("auth:", "session:")):
        return value if value.split(":", 1)[1].strip().lower() == pid else None
    env_name = value[4:].strip()
    return value if env_name.isidentifier() else None


def _sanitize_custom_provider_credentials(
    custom_providers,
    auth_service,
    *,
    allow_secret_import: bool = False,
):
    if not isinstance(custom_providers, list):
        return [], True

    sanitized = []
    changed = False
    for raw_entry in custom_providers[:_MAX_CUSTOM_PROVIDERS]:
        if not isinstance(raw_entry, dict):
            changed = True
            continue
        entry = dict(raw_entry)
        name = str(entry.get("name", "") or "").strip().lower()[:64]
        raw_key = entry.get("api_key", "")
        credential_ref = entry.get("credential_ref")
        safe_ref = _safe_ref_for_provider(credential_ref, name)

        if isinstance(raw_key, str):
            raw_key = raw_key.strip()
        else:
            raw_key = ""
            changed = True

        if raw_key.startswith(_CREDENTIAL_REFERENCE_PREFIXES):
            candidate = _safe_ref_for_provider(raw_key, name)
            safe_ref = candidate or safe_ref
            changed = True
        elif raw_key:
            if allow_secret_import and name:
                # This path is used only by explicit save actions (UI/command),
                # never while loading repository configuration.
                safe_ref = auth_service.login(
                    name, raw_key, method="api_key"
                ).credential_ref
            changed = True

        entry["api_key"] = safe_ref or ""
        if safe_ref:
            entry["credential_ref"] = safe_ref
        else:
            entry.pop("credential_ref", None)

        if len(name) == 0:
            changed = True
            continue
        entry["name"] = name
        for key in ("base_url", "backend", "protocol"):
            value = entry.get(key)
            if value is not None and not isinstance(value, str):
                entry[key] = str(value)[:2048]
                changed = True
        sanitized.append(entry)

    if len(custom_providers) > _MAX_CUSTOM_PROVIDERS:
        changed = True
    return sanitized, changed


class TaskRouter:
    def __init__(self, root_dir: str = "."):
        self.classifier = TaskClassifier()
        self.root_dir = root_dir
        self.config = self._load_config(root_dir)

    def load_config(self, root_dir: str) -> RouterConfig:
        return self._load_config(root_dir)

    @staticmethod
    def _defaults() -> RouterConfig:
        return RouterConfig(
            profiles={
                name: replace(profile)
                for name, profile in DEFAULT_ROUTER_CONFIG.profiles.items()
            },
            routing=dict(DEFAULT_ROUTER_CONFIG.routing),
        )

    def _load_config(self, root_dir: str) -> RouterConfig:
        defaults = self._defaults()
        fs = WorkspaceFileSystem(root_dir, max_file_bytes=_MAX_ROUTER_BYTES)
        try:
            raw = fs.read(".kitt-router.json", max_bytes=_MAX_ROUTER_BYTES).content
        except FileNotFoundError:
            return defaults
        except Exception:
            # Symlink/special/oversized workspace config is ignored fail-closed.
            return defaults

        try:
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                return defaults
            profiles_raw = data.get("profiles", {})
            if not isinstance(profiles_raw, dict):
                profiles_raw = {}

            profiles: Dict[str, ModelProfile] = {}
            sanitized_profiles = {}
            changed = False
            for key, value in list(profiles_raw.items())[:_MAX_PROFILES]:
                if not isinstance(key, str) or not isinstance(value, dict):
                    changed = True
                    continue
                item = {k: v for k, v in value.items() if k in _PROFILE_FIELDS}
                backend = str(item.get("backend", "ollama") or "ollama").strip().lower()[:64]
                model = str(item.get("model", "") or "").strip()[:512]
                if not model:
                    changed = True
                    continue
                item["backend"] = backend
                item["model"] = model

                raw_key = item.get("api_key", "")
                safe_ref = _safe_ref_for_provider(item.get("credential_ref"), backend)
                if isinstance(raw_key, str) and raw_key.startswith(_CREDENTIAL_REFERENCE_PREFIXES):
                    safe_ref = _safe_ref_for_provider(raw_key, backend) or safe_ref
                    if raw_key:
                        changed = True
                elif raw_key:
                    changed = True
                # Literal secrets from repository configuration are never
                # imported into global auth state.
                item["api_key"] = ""
                if safe_ref:
                    item["credential_ref"] = safe_ref
                else:
                    item.pop("credential_ref", None)

                base_url = item.get("base_url")
                if base_url is not None:
                    if not isinstance(base_url, str) or len(base_url) > 2048:
                        item["base_url"] = None
                        changed = True
                    elif base_url and not base_url.startswith(("http://", "https://")):
                        item["base_url"] = None
                        changed = True
                name = key[:64]
                sanitized_profiles[name] = {
                    "backend": item.get("backend"),
                    "model": item.get("model"),
                    "base_url": item.get("base_url"),
                    "credential_ref": item.get("credential_ref"),
                    "protocol": item.get("protocol"),
                    "context_window": item.get("context_window"),
                    "max_output_tokens": item.get("max_output_tokens"),
                    "temperature": item.get("temperature"),
                    "supports_tools": item.get("supports_tools"),
                    "supports_json": item.get("supports_json"),
                    "keep_alive": item.get("keep_alive"),
                    "request_timeout_seconds": item.get("request_timeout_seconds"),
                }
                profiles[name] = ModelProfile(**item)

            routing_raw = data.get("routing", {})
            routing = {}
            if isinstance(routing_raw, dict):
                for task, profile in list(routing_raw.items())[:_MAX_ROUTING]:
                    if isinstance(task, str) and isinstance(profile, str):
                        routing[task[:128]] = profile[:64]
                    else:
                        changed = True

            custom, custom_changed = _sanitize_custom_provider_credentials(
                data.get("custom_providers", []),
                ProviderAuthService(),
                allow_secret_import=True,
            )
            changed = changed or custom_changed
            loaded = RouterConfig(
                profiles={**defaults.profiles, **profiles},
                routing={**defaults.routing, **routing},
                custom_providers=custom,
            )
            if changed:
                sanitized = {
                    "profiles": sanitized_profiles,
                    "routing": routing,
                    "custom_providers": custom,
                }
                encoded = json.dumps(sanitized, indent=2, ensure_ascii=False)
                fs.atomic_write(".kitt-router.json", encoded, max_bytes=_MAX_ROUTER_BYTES)
            return loaded
        except Exception:
            return defaults

    def save_config(self, root_dir: str) -> None:
        """Persist shareable config; secrets remain in user-owned auth storage."""
        auth_service = ProviderAuthService()
        profiles_data = {}
        for name, profile in list(self.config.profiles.items())[:_MAX_PROFILES]:
            backend = (profile.backend or "ollama").strip().lower()
            credential_ref = _safe_ref_for_provider(profile.credential_ref, backend)
            if not credential_ref and profile.api_key:
                if profile.api_key.startswith(_CREDENTIAL_REFERENCE_PREFIXES):
                    credential_ref = _safe_ref_for_provider(profile.api_key, backend)
                else:
                    state = auth_service.state(backend)
                    credential_ref = (
                        state.credential_ref
                        if state.is_valid
                        else None
                    )
            profiles_data[name] = {
                "backend": profile.backend,
                "model": profile.model,
                "base_url": profile.base_url,
                "credential_ref": credential_ref,
                "protocol": profile.protocol,
                "context_window": profile.context_window,
                "max_output_tokens": profile.max_output_tokens,
                "temperature": profile.temperature,
                "supports_tools": profile.supports_tools,
                "supports_json": profile.supports_json,
                "keep_alive": profile.keep_alive,
                "request_timeout_seconds": profile.request_timeout_seconds,
            }

        custom, _ = _sanitize_custom_provider_credentials(
            getattr(self.config, "custom_providers", []),
            auth_service,
            allow_secret_import=True,
        )
        self.config.custom_providers = custom
        data = {
            "profiles": profiles_data,
            "routing": dict(list(self.config.routing.items())[:_MAX_ROUTING]),
            "custom_providers": custom,
        }
        encoded = json.dumps(data, indent=2, ensure_ascii=False)
        WorkspaceFileSystem(
            root_dir,
            max_file_bytes=_MAX_ROUTER_BYTES,
        ).atomic_write(".kitt-router.json", encoded, max_bytes=_MAX_ROUTER_BYTES)

    def resolve_profile_for_task(self, task_type: TaskType) -> Tuple[str, ModelProfile]:
        profile_name = self.config.routing.get(task_type)
        if not profile_name:
            if task_type in {"context-gather", "summarize"}:
                profile_name = self.config.routing.get("context", "context")
            elif task_type in {"code-generation", "code-edit"}:
                profile_name = self.config.routing.get(
                    "code_generation", self.config.routing.get("edit", "execute")
                )
            else:
                profile_name = self.config.routing.get("chat", "execute")
        profile = self.config.profiles.get(profile_name) or list(self.config.profiles.values())[0]
        return profile_name, profile

    def route(self, step: TaskStep) -> Tuple[TaskType, str, ModelProfile]:
        task_type = self.classifier.classify(step)
        profile_name, profile = self.resolve_profile_for_task(task_type)
        return task_type, profile_name, profile
