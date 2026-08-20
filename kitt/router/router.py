"""Task router and profile configuration management with secure credential references."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Dict, Tuple

from kitt.domain.entities import ModelProfile, RouterConfig, TaskStep, TaskType
from kitt.llm.auth import ProviderAuthService
from kitt.router.classifier import TaskClassifier
from kitt.security.credentials import atomic_write_secure

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


def _sanitize_custom_provider_credentials(custom_providers, auth_service):
    """Move literal custom-provider secrets to CredentialStore."""
    if not isinstance(custom_providers, list):
        return [], True

    sanitized = []
    changed = False

    for raw_entry in custom_providers:
        if not isinstance(raw_entry, dict):
            changed = True
            continue

        entry = dict(raw_entry)
        name = str(entry.get("name", "") or "").strip().lower()
        raw_key = entry.get("api_key", "")
        credential_ref = entry.get("credential_ref")

        if raw_key is None:
            raw_key = ""
        if not isinstance(raw_key, str):
            raw_key = ""
            changed = True
        raw_key = raw_key.strip()

        if credential_ref is not None and not isinstance(credential_ref, str):
            credential_ref = None
            changed = True
        if isinstance(credential_ref, str):
            credential_ref = credential_ref.strip() or None

        if raw_key:
            if raw_key.startswith(_CREDENTIAL_REFERENCE_PREFIXES):
                safe_ref = raw_key
            elif name:
                safe_ref = auth_service.login(
                    name,
                    raw_key,
                    method="api_key",
                ).credential_ref
                changed = True
            else:
                safe_ref = ""
                changed = True

            if entry.get("api_key") != safe_ref:
                changed = True
            entry["api_key"] = safe_ref
            if safe_ref:
                if entry.get("credential_ref") != safe_ref:
                    changed = True
                entry["credential_ref"] = safe_ref
            else:
                entry.pop("credential_ref", None)
        elif credential_ref:
            if credential_ref.startswith(_CREDENTIAL_REFERENCE_PREFIXES):
                safe_ref = credential_ref
            elif name:
                safe_ref = auth_service.login(
                    name,
                    credential_ref,
                    method="api_key",
                ).credential_ref
                changed = True
            else:
                safe_ref = ""
                changed = True

            entry["credential_ref"] = safe_ref
            entry["api_key"] = safe_ref
        else:
            if entry.get("api_key") not in ("", None):
                changed = True
            entry["api_key"] = ""
            entry.pop("credential_ref", None)

        sanitized.append(entry)

    return sanitized, changed


class TaskRouter:
    """Routes subtasks between fast context models and large execution models."""

    def __init__(self, root_dir: str = "."):
        self.classifier = TaskClassifier()
        self.root_dir = root_dir
        self.config = self._load_config(root_dir)

    def load_config(self, root_dir: str) -> RouterConfig:
        return self._load_config(root_dir)

    def _load_config(self, root_dir: str) -> RouterConfig:
        defaults = RouterConfig(
            profiles={name: replace(profile) for name, profile in DEFAULT_ROUTER_CONFIG.profiles.items()},
            routing=dict(DEFAULT_ROUTER_CONFIG.routing),
        )
        config_path = Path(root_dir) / ".kitt-router.json"
        if not config_path.exists():
            return defaults

        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            profiles_raw = data.get("profiles", {})
            profiles: Dict[str, ModelProfile] = {}
            needs_migration = False

            auth_service = ProviderAuthService()

            for k, v in profiles_raw.items():
                backend = v.get("backend", "ollama")
                raw_key = v.get("api_key", "")
                cred_ref = v.get("credential_ref")

                # Detect legacy plain text secrets and migrate safely
                if raw_key and not raw_key.startswith(("env:", "auth:", "session:")):
                    needs_migration = True
                    auth_state = auth_service.login(backend, raw_key)
                    cred_ref = auth_state.credential_ref
                    v["api_key"] = ""
                    v["credential_ref"] = cred_ref
                elif not cred_ref and raw_key.startswith(("env:", "auth:", "session:")):
                    cred_ref = raw_key
                    v["credential_ref"] = cred_ref
                    v["api_key"] = ""

                profiles[k] = ModelProfile(**v)

            routing = data.get("routing", {})
            custom_providers, custom_migrated = _sanitize_custom_provider_credentials(
                data.get("custom_providers", []),
                auth_service,
            )
            needs_migration = needs_migration or custom_migrated
            loaded_config = RouterConfig(
                profiles={**defaults.profiles, **profiles},
                routing={**defaults.routing, **routing},
                custom_providers=custom_providers,
            )

            if needs_migration:
                # Save sanitized config back immediately
                self.config = loaded_config
                self.save_config(root_dir)

            return loaded_config
        except Exception:
            return defaults

    def save_config(self, root_dir: str) -> None:
        """Persist current routing securely using credential_ref instead of plain text secrets."""
        config_path = Path(root_dir) / ".kitt-router.json"
        profiles_data = {}
        for name, profile in self.config.profiles.items():
            backend = profile.backend or "ollama"
            cred_ref = profile.credential_ref
            if not cred_ref and profile.api_key:
                if profile.api_key.startswith(("env:", "auth:", "session:")):
                    cred_ref = profile.api_key
                else:
                    env_var = ProviderAuthService.get_default_env_var(backend)
                    cred_ref = f"env:{env_var}"

            profile_dict = {
                "backend": profile.backend,
                "model": profile.model,
                "base_url": profile.base_url,
                "credential_ref": cred_ref,
                "protocol": profile.protocol,
                "context_window": profile.context_window,
                "max_output_tokens": profile.max_output_tokens,
                "temperature": profile.temperature,
                "supports_tools": profile.supports_tools,
                "supports_json": profile.supports_json,
                "keep_alive": profile.keep_alive,
                "request_timeout_seconds": profile.request_timeout_seconds,
            }
            profiles_data[name] = profile_dict

        auth_service = ProviderAuthService()
        custom_providers, _ = _sanitize_custom_provider_credentials(
            getattr(self.config, "custom_providers", []),
            auth_service,
        )
        original_custom = getattr(self.config, "custom_providers", [])
        if isinstance(original_custom, list):
            shared = [
                item for item in original_custom
                if isinstance(item, dict)
            ]
            for existing, sanitized_item in zip(shared, custom_providers):
                existing.clear()
                existing.update(sanitized_item)
            original_custom[:] = [dict(item) for item in custom_providers]
            custom_providers = original_custom
        self.config.custom_providers = custom_providers

        data = {
            "profiles": profiles_data,
            "routing": self.config.routing,
            "custom_providers": custom_providers,
        }
        atomic_write_secure(config_path, json.dumps(data, indent=2))

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
