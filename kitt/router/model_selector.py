import json
import urllib.request
import urllib.error
from pathlib import Path
from typing import List, Dict, Any, Tuple
from kitt.domain.entities import ModelProfile, RouterConfig
from kitt.router.router import TaskRouter

ROLES = ["main_chat", "context", "commit", "edit", "code_generation"]

def fetch_provider_models(provider: str, base_url: str = "", api_key: str = "", timeout: float = 4.0) -> List[str]:
    """Issues an HTTP GET request to discover models dynamically from the provider API."""
    provider = (provider or "").strip().lower()
    base_url = (base_url or "").strip().rstrip("/")
    headers = {"User-Agent": "Kitt-CLI"}

    try:
        # 1. Ollama (/api/tags)
        if provider == "ollama" or (base_url and ":11434" in base_url):
            url = f"{base_url or 'http://localhost:11434'}/api/tags"
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                data = json.loads(response.read().decode('utf-8'))
                models = data.get("models", [])
                names = [m.get("name") for m in models if isinstance(m, dict) and m.get("name")]
                if names:
                    return names

        # 2. Anthropic (/v1/models)
        elif provider == "anthropic":
            url = f"{base_url or 'https://api.anthropic.com'}/v1/models"
            if api_key:
                headers["x-api-key"] = api_key
            headers["anthropic-version"] = "2023-06-01"
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                data = json.loads(response.read().decode('utf-8'))
                models = data.get("data", [])
                names = [m.get("id") for m in models if isinstance(m, dict) and m.get("id")]
                if names:
                    return names

        # 3. Gemini / Google (/v1beta/models)
        elif provider == "gemini":
            base = base_url or "https://generativelanguage.googleapis.com"
            url = f"{base}/v1beta/models?key={api_key}" if api_key else f"{base}/v1beta/models"
            if api_key:
                headers["x-goog-api-key"] = api_key
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                data = json.loads(response.read().decode('utf-8'))
                models = data.get("models", [])
                names = []
                for m in models:
                    if isinstance(m, dict):
                        methods = m.get("supportedGenerationMethods", [])
                        if not methods or "generateContent" in methods:
                            m_name = m.get("name", "")
                            if m_name.startswith("models/"):
                                m_name = m_name[7:]
                            if m_name:
                                names.append(m_name)
                if names:
                    return names

        # 4. OpenAI / LMStudio / DeepSeek / Groq / Together / Mistral / OpenRouter / xAI / Fireworks / Cohere / Azure / Generic
        else:
            if not base_url:
                defaults_url = {
                    "openai": "https://api.openai.com",
                    "deepseek": "https://api.deepseek.com",
                    "groq": "https://api.groq.com/openai",
                    "together": "https://api.together.xyz",
                    "mistral": "https://api.mistral.ai",
                    "openrouter": "https://openrouter.ai/api",
                    "xai": "https://api.xai.com",
                    "fireworks": "https://api.fireworks.ai/inference",
                    "cohere": "https://api.cohere.com",
                    "lmstudio": "http://localhost:1234",
                    "antigravity": "https://api.antigravity.dev",
                }
                base_url = defaults_url.get(provider, "http://localhost:11434")

            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            for endpoint_path in ["/v1/models", "/models"]:
                try:
                    url = f"{base_url}{endpoint_path}"
                    req = urllib.request.Request(url, headers=headers)
                    with urllib.request.urlopen(req, timeout=timeout) as response:
                        data = json.loads(response.read().decode('utf-8'))
                        raw_data = data.get("data", []) if isinstance(data, dict) else data
                        if isinstance(raw_data, list):
                            names = [
                                item.get("id") or item.get("name") 
                                for item in raw_data 
                                if isinstance(item, dict) and (item.get("id") or item.get("name"))
                            ]
                            if names:
                                return names
                except Exception:
                    continue
    except Exception:
        pass

    return []


class ModelConfigurator:
    """Discovers provider models and manages role assignment including Main Chat Model."""

    def __init__(self, root_dir: str = "."):
        self.root_dir = root_dir
        self.router = TaskRouter(root_dir=root_dir)

    def fetch_ollama_models(self, base_url: str = "http://localhost:11434") -> List[str]:
        return fetch_provider_models("ollama", base_url)

    def assign_roles(
        self,
        model_a: str,
        model_b: str,
        model_a_roles: List[str],
        backend_a: str = "ollama",
        base_url_a: str = "http://localhost:11434",
        api_key_a: str = "",
        backend_b: str = "ollama",
        base_url_b: str = "http://localhost:11434",
        api_key_b: str = ""
    ) -> RouterConfig:
        model_b_roles = [r for r in ROLES if r not in model_a_roles]

        profiles = {
            "model_a": ModelProfile(backend=backend_a, model=model_a, base_url=base_url_a, api_key=api_key_a),
            "model_b": ModelProfile(backend=backend_b, model=model_b, base_url=base_url_b, api_key=api_key_b),
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

    def _setup_model_interactively(self, label: str) -> Tuple[str, str, str, str]:
        """Returns (backend, base_url, api_key, model_name)"""
        from kitt.cli.ui import prompt_dropdown
        print(f"\n\033[1;36m=== Setup {label} ===\033[0m")
        providers = ["ollama", "lmstudio", "antigravity", "gemini", "openai", "anthropic"]
        backend = prompt_dropdown(f"Select provider for {label} [ollama/lmstudio/antigravity/gemini/openai/anthropic] (default: ollama): ", providers, default="ollama").lower()
        
        base_url = ""
        api_key = ""
        
        if backend == "ollama":
            base_url = input("Ollama Base URL (default: http://localhost:11434): ").strip() or "http://localhost:11434"
            print(f"\nFetching models from {backend} at {base_url}...")
            available_models = self.fetch_ollama_models(base_url)
        elif backend == "lmstudio":
            base_url = input("LMStudio Base URL (default: http://localhost:1234): ").strip() or "http://localhost:1234"
            api_key = input("API Key (leave blank if local): ").strip()
            print(f"\nFetching models from {backend} at {base_url}...")
            available_models = self.fetch_ollama_models(base_url)
        elif backend == "openai" or backend == "openai-codex":
            base_url = input("OpenAI Base URL (default: https://api.openai.com): ").strip() or "https://api.openai.com"
            api_key = input("OpenAI API Key: ").strip()
            available_models = ["gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo", "gpt-4o-mini"]
        elif backend == "anthropic":
            base_url = input("Anthropic Base URL (default: https://api.anthropic.com): ").strip() or "https://api.anthropic.com"
            api_key = input("Anthropic API Key: ").strip()
            available_models = ["claude-3-5-sonnet-20240620", "claude-3-opus-20240229", "claude-3-haiku-20240307"]
        elif backend == "antigravity" or backend == "gemini":
            backend = "antigravity"
            base_url = input("Antigravity/Gemini Base URL (default: https://api.antigravity.dev): ").strip() or "https://api.antigravity.dev"
            api_key = input("API Key: ").strip()
            available_models = ["ag-pro", "ag-flash", "ag-lite", "gemini-1.5-pro", "gemini-1.5-flash"]
        else:
            base_url = input("Base URL: ").strip()
            api_key = input("API Key: ").strip()
            available_models = ["model-1", "model-2"]

        if not available_models:
            print("\033[33mWarning: No models found. Adding fallback defaults.\033[0m")
            available_models = ["qwen2.5:7b-instruct", "qwen2.5:32b-instruct"]

        print(f"\n\033[1;37mAvailable Models for {label}:\033[0m")
        for idx, m in enumerate(available_models, start=1):
            print(f"  [{idx}] {m}")

        raw_m = prompt_dropdown(f"\nSelect {label} [name or number]: ", available_models)
        if raw_m.isdigit() and 1 <= int(raw_m) <= len(available_models):
            model_name = available_models[int(raw_m) - 1]
        else:
            model_name = raw_m or available_models[0]
            
        return backend, base_url, api_key, model_name

    def run_interactive_setup(self):
        from kitt.cli.ui import prompt_dropdown
        print("\n\033[1;36m=== K.I.T.T. Multi-Model Role & Provider Setup ===\033[0m")
        
        backend_a, base_url_a, api_key_a, model_a = self._setup_model_interactively("Model A (Principal/Contexto)")
        backend_b, base_url_b, api_key_b, model_b = self._setup_model_interactively("Model B (Code Generation/Edits)")

        print(f"\nConfiguring Model A (\033[36m{model_a}\033[0m) roles:")
        print("Available roles: [1] main_chat (Modelo Principal)  [2] context  [3] commit  [4] edit  [5] code_generation")
        raw_choices = prompt_dropdown("Select roles for Model A (e.g., '1 2 3'): ", ["1 2 3", "1", "2 3 4 5", "1 2 3 4 5"])

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

        config = self.assign_roles(
            model_a, model_b, chosen_roles, 
            backend_a=backend_a, base_url_a=base_url_a, api_key_a=api_key_a,
            backend_b=backend_b, base_url_b=base_url_b, api_key_b=api_key_b
        )

        print("\n\033[1;32m✓ Multi-Model Configuration Saved to .kitt-router.json!\033[0m")
        print(f"  Model A (\033[36m{model_a}\033[0m): " + ", ".join([f"[\033[32mx\033[0m] {r}" for r in chosen_roles]))
        remaining = [r for r in ROLES if r not in chosen_roles]
        print(f"  Model B (\033[33m{model_b}\033[0m): " + ", ".join([f"[\033[32mx\033[0m] {r}" for r in remaining]))
        print()
