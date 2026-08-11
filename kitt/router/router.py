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

    def route(self, step: TaskStep) -> Tuple[TaskType, str, ModelProfile]:
        task_type = self.classifier.classify(step)
        profile_name = self.config.routing.get(task_type, "execute")
        profile = self.config.profiles.get(profile_name, self.config.profiles["execute"])
        return task_type, profile_name, profile
