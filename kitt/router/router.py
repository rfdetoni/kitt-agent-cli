import json
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
        config_path = Path(root_dir) / ".kitt-router.json"
        if config_path.exists():
            try:
                data = json.loads(config_path.read_text(encoding='utf-8'))
                profiles = {
                    k: ModelProfile(**v) for k, v in data.get("profiles", {}).items()
                }
                routing = data.get("routing", {})
                return RouterConfig(
                    profiles={**DEFAULT_ROUTER_CONFIG.profiles, **profiles},
                    routing={**DEFAULT_ROUTER_CONFIG.routing, **routing}
                )
            except Exception:
                return DEFAULT_ROUTER_CONFIG
        return DEFAULT_ROUTER_CONFIG

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
