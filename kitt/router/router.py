import json
from dataclasses import replace
from pathlib import Path
from typing import Dict, Tuple
from kitt.domain.entities import TaskStep, TaskType, ModelProfile, RouterConfig
from kitt.router.classifier import TaskClassifier

DEFAULT_ROUTER_CONFIG = RouterConfig(
    profiles={
        "context": ModelProfile(backend="ollama", model="qwen2.5:7b-instruct"),
        "execute": ModelProfile(backend="ollama", model="qwen2.5:32b-instruct")
    },
    routing={
        "context-gather": "context",
        "summarize": "context",
        "code-generation": "execute",
        "code-edit": "execute",
        "validate-diff": "context"
    }
)

class TaskRouter:
    """Routes subtasks between fast context models and large execution models."""

    def __init__(self, root_dir: str = "."):
        self.classifier = TaskClassifier()
        self.config = self._load_config(root_dir)

    def _load_config(self, root_dir: str) -> RouterConfig:
        defaults = RouterConfig(
            profiles={name: replace(profile) for name, profile in DEFAULT_ROUTER_CONFIG.profiles.items()},
            routing=dict(DEFAULT_ROUTER_CONFIG.routing),
        )
        config_path = Path(root_dir) / ".kitt-router.json"
        if config_path.exists():
            try:
                data = json.loads(config_path.read_text(encoding='utf-8'))
                profiles = {
                    k: ModelProfile(**v) for k, v in data.get("profiles", {}).items()
                }
                routing = data.get("routing", {})
                return RouterConfig(
                    profiles={**defaults.profiles, **profiles},
                    routing={**defaults.routing, **routing}
                )
            except Exception:
                return defaults
        return defaults

    def save_config(self, root_dir: str) -> None:
        """Persist current routing without rebuilding the runtime router."""
        from kitt.security.credentials import CredentialResolver, atomic_write_secure

        config_path = Path(root_dir) / ".kitt-router.json"
        profiles_data = {}
        for name, profile in self.config.profiles.items():
            profile_dict = {
                "backend": profile.backend,
                "model": profile.model,
                "base_url": profile.base_url,
                "api_key": profile.api_key if (profile.api_key and profile.api_key.startswith(("env:", "session:"))) else (f"env:OPENAI_API_KEY" if profile.api_key else None),
                "context_window": profile.context_window,
                "max_output_tokens": profile.max_output_tokens,
                "temperature": profile.temperature,
                "supports_tools": profile.supports_tools,
                "supports_json": profile.supports_json,
                "keep_alive": profile.keep_alive,
                "request_timeout_seconds": profile.request_timeout_seconds,
            }
            profiles_data[name] = profile_dict

        data = {
            "profiles": profiles_data,
            "routing": self.config.routing,
        }
        atomic_write_secure(config_path, json.dumps(data, indent=2))

    def resolve_profile_for_task(self, task_type: TaskType) -> Tuple[str, ModelProfile]:
        profile_name = self.config.routing.get(task_type)
        if not profile_name:
            if task_type in {"context-gather", "summarize"}:
                profile_name = self.config.routing.get("context", "context")
            elif task_type in {"code-generation", "code-edit"}:
                profile_name = self.config.routing.get("code_generation", self.config.routing.get("edit", "execute"))
            else:
                profile_name = self.config.routing.get("chat", "execute")

        profile = self.config.profiles.get(profile_name) or list(self.config.profiles.values())[0]
        return profile_name, profile

    def route(self, step: TaskStep) -> Tuple[TaskType, str, ModelProfile]:
        task_type = self.classifier.classify(step)
        profile_name, profile = self.resolve_profile_for_task(task_type)
        return task_type, profile_name, profile
