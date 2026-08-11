import json
import urllib.request
import urllib.error
from pathlib import Path
from typing import List, Dict, Any, Tuple
from kitt.domain.entities import ModelProfile, RouterConfig
from kitt.router.router import TaskRouter

ROLES = ["main_chat", "context", "commit", "edit", "code_generation"]

class ModelConfigurator:
    """Discovers provider models and manages interactive role assignment including Main Chat Model."""

    def __init__(self, root_dir: str = "."):
        self.root_dir = root_dir
        self.router = TaskRouter(root_dir=root_dir)

    def fetch_ollama_models(self, base_url: str = "http://localhost:11434") -> List[str]:
        url = f"{base_url.rstrip('/')}/api/tags"
        req = urllib.request.Request(url, headers={"User-Agent": "Kitt-CLI"})
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode('utf-8'))
                models = data.get("models", [])
                return [m.get("name") for m in models if "name" in m]
        except Exception:
            return ["qwen2.5:7b-instruct", "qwen2.5:32b-instruct"]

    def assign_roles(
        self,
        model_a: str,
        model_b: str,
        model_a_roles: List[str],
        backend: str = "ollama",
        base_url: str = "http://localhost:11434"
    ) -> RouterConfig:
        model_b_roles = [r for r in ROLES if r not in model_a_roles]

        profiles = {
            "model_a": ModelProfile(backend=backend, model=model_a, base_url=base_url),
            "model_b": ModelProfile(backend=backend, model=model_b, base_url=base_url),
        }

        routing: Dict[str, str] = {}
        for role in model_a_roles:
            if role == "main_chat":
                routing["chat"] = "model_a"
            elif role == "context":
                routing["context-gather"] = "model_a"
                routing["summarize"] = "model_a"
            elif role == "commit":
                routing["validate-diff"] = "model_a"
            elif role == "edit":
                routing["code-edit"] = "model_a"
            elif role == "code_generation":
                routing["code-generation"] = "model_a"

        for role in model_b_roles:
            if role == "main_chat":
                routing["chat"] = "model_b"
            elif role == "context":
                routing["context-gather"] = "model_b"
                routing["summarize"] = "model_b"
            elif role == "commit":
                routing["validate-diff"] = "model_b"
            elif role == "edit":
                routing["code-edit"] = "model_b"
            elif role == "code_generation":
                routing["code-generation"] = "model_b"

        config = RouterConfig(profiles=profiles, routing=routing)
        self.save_config(config)
        return config

    def save_config(self, config: RouterConfig):
        config_path = Path(self.root_dir) / ".kitt-router.json"
        data = {
            "profiles": {
                k: {
                    "backend": v.backend,
                    "model": v.model,
                    "base_url": v.base_url,
                    "api_key": v.api_key
                } for k, v in config.profiles.items()
            },
            "routing": config.routing
        }
        config_path.write_text(json.dumps(data, indent=2), encoding='utf-8')

    def run_interactive_setup(self):
        print("\n\033[1;36m=== K.I.T.T. Multi-Model Role & Provider Setup ===\033[0m")
        backend = input("Select provider [ollama/openai] (default: ollama): ").strip() or "ollama"
        base_url = "http://localhost:11434"
        if backend == "ollama":
            base_url = input("Ollama Base URL (default: http://localhost:11434): ").strip() or "http://localhost:11434"

        print(f"\nFetching models from {backend} at {base_url}...")
        available_models = self.fetch_ollama_models(base_url)

        if len(available_models) < 2:
            print("\033[33mWarning: Less than 2 models found. Adding default models for demo.\033[0m")
            available_models = list(set(available_models + ["qwen2.5:7b-instruct", "qwen2.5:32b-instruct"]))

        print("\n\033[1;37mAvailable Models:\033[0m")
        for idx, m in enumerate(available_models, start=1):
            print(f"  [{idx}] {m}")

        try:
            idx_a = int(input("\nSelect Model A [number]: ")) - 1
            idx_b = int(input("Select Model B [number]: ")) - 1
            model_a = available_models[idx_a]
            model_b = available_models[idx_b]
        except (ValueError, IndexError):
            print("\033[31mInvalid model selection. Using defaults.\033[0m")
            model_a = available_models[0]
            model_b = available_models[1] if len(available_models) > 1 else available_models[0]

        print(f"\nConfiguring Model A (\033[36m{model_a}\033[0m) roles:")
        print("Available roles: [1] main_chat (Modelo Principal)  [2] context  [3] commit  [4] edit  [5] code_generation")
        raw_choices = input("Select roles for Model A (e.g., '1 2 3' for main_chat, context & commit): ").strip()

        role_map = {
            "1": "main_chat",
            "2": "context",
            "3": "commit",
            "4": "edit",
            "5": "code_generation"
        }
        chosen_roles = [role_map[c] for c in raw_choices.split() if c in role_map]

        if not chosen_roles:
            chosen_roles = ["main_chat", "context", "commit"]

        config = self.assign_roles(model_a, model_b, chosen_roles, backend=backend, base_url=base_url)

        print("\n\033[1;32m✓ Multi-Model Configuration Saved to .kitt-router.json!\033[0m")
        print(f"  Model A (\033[36m{model_a}\033[0m): " + ", ".join([f"[\033[32mx\033[0m] {r}" for r in chosen_roles]))
        remaining = [r for r in ROLES if r not in chosen_roles]
        print(f"  Model B (\033[33m{model_b}\033[0m): " + ", ".join([f"[\033[32mx\033[0m] {r}" for r in remaining]))
        print()
