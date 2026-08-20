"""Catalog service for AI providers and models with Models.dev and offline cache."""
from __future__ import annotations

import json
import os
import tempfile
import time
import urllib.error
import urllib.request
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from kitt.llm.http_security import secure_urlopen
from kitt.llm.domain import ModelDescriptor, ProviderDescriptor

MODELS_DEV_URL = "https://models.dev/api.json"
CACHE_VERSION = 1
DEFAULT_CACHE_TTL_SECONDS = 86400 * 7  # 7 days

BUILTIN_PROVIDERS: List[ProviderDescriptor] = [
    ProviderDescriptor(
        id="ollama",
        name="Ollama",
        protocol="ollama-chat",
        base_url="http://localhost:11434",
        env_vars=("OLLAMA_HOST",),
        auth_methods=(),
        local=True,
        supports_model_discovery=True,
        supports_custom_base_url=True,
        source="builtin",
    ),
    ProviderDescriptor(
        id="lmstudio",
        name="LM Studio",
        protocol="openai-chat-completions",
        base_url="http://localhost:1234",
        env_vars=("LM_STUDIO_HOST",),
        auth_methods=("api_key",),
        local=True,
        supports_model_discovery=True,
        supports_custom_base_url=True,
        source="builtin",
    ),
    ProviderDescriptor(
        id="openai",
        name="OpenAI",
        protocol="openai-chat-completions",
        base_url="https://api.openai.com",
        env_vars=("OPENAI_API_KEY",),
        auth_methods=("api_key",),
        supports_model_discovery=True,
        supports_custom_base_url=True,
        source="builtin",
    ),
    ProviderDescriptor(
        id="anthropic",
        name="Anthropic",
        protocol="anthropic-messages",
        base_url="https://api.anthropic.com",
        env_vars=("ANTHROPIC_API_KEY",),
        auth_methods=("api_key",),
        supports_model_discovery=True,
        supports_custom_base_url=False,
        source="builtin",
    ),
    ProviderDescriptor(
        id="gemini",
        name="Google Gemini",
        protocol="gemini-generate-content",
        base_url="https://generativelanguage.googleapis.com",
        env_vars=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        auth_methods=("api_key",),
        supports_model_discovery=True,
        supports_custom_base_url=True,
        source="builtin",
    ),
    ProviderDescriptor(
        id="openrouter",
        name="OpenRouter",
        protocol="openai-chat-completions",
        base_url="https://openrouter.ai/api",
        env_vars=("OPENROUTER_API_KEY",),
        auth_methods=("api_key",),
        supports_model_discovery=True,
        supports_custom_base_url=True,
        source="builtin",
    ),
    ProviderDescriptor(
        id="groq",
        name="Groq",
        protocol="openai-chat-completions",
        base_url="https://api.groq.com/openai",
        env_vars=("GROQ_API_KEY",),
        auth_methods=("api_key",),
        supports_model_discovery=True,
        supports_custom_base_url=True,
        source="builtin",
    ),
    ProviderDescriptor(
        id="deepseek",
        name="DeepSeek",
        protocol="openai-chat-completions",
        base_url="https://api.deepseek.com",
        env_vars=("DEEPSEEK_API_KEY",),
        auth_methods=("api_key",),
        supports_model_discovery=True,
        supports_custom_base_url=True,
        source="builtin",
    ),
    ProviderDescriptor(
        id="antigravity",
        name="Google Antigravity",
        protocol="openai-chat-completions",
        base_url="https://api.antigravity.dev",
        env_vars=("ANTIGRAVITY_API_KEY", "GEMINI_API_KEY"),
        auth_methods=("api_key",),
        supports_model_discovery=True,
        supports_custom_base_url=True,
        source="builtin",
    ),
]


class ProviderCatalogService:
    """Central provider and model catalog backed by Models.dev with robust offline caching."""

    def __init__(
        self,
        cache_dir: Optional[str] = None,
        ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
        fetch_url: str = MODELS_DEV_URL,
        timeout: float = 5.0,
    ):
        self.cache_dir = Path(cache_dir or (Path.home() / ".kitt" / "cache")).resolve()
        self.cache_file = self.cache_dir / "models.dev.json"
        self.ttl_seconds = ttl_seconds
        self.fetch_url = fetch_url
        self.timeout = timeout
        self._providers_map: Dict[str, ProviderDescriptor] = {}
        self._models_map: Dict[str, List[ModelDescriptor]] = {}
        self._load_builtins()
        self._load_cache_or_builtins()

    def _load_builtins(self) -> None:
        for p in BUILTIN_PROVIDERS:
            self._providers_map[p.id] = p
            if p.id not in self._models_map:
                self._models_map[p.id] = []

    def _load_cache_or_builtins(self) -> None:
        if self.cache_file.exists():
            try:
                data = json.loads(self.cache_file.read_text(encoding="utf-8"))
                self._parse_and_merge_catalog(data)
                return
            except Exception:
                pass
        try:
            self.refresh(force=True)
        except Exception:
            pass

    def _parse_and_merge_catalog(self, raw_data: Dict[str, Any]) -> None:
        """Parses Models.dev json or cached catalog into descriptors."""
        if not isinstance(raw_data, dict):
            return

        if "providers" in raw_data and isinstance(raw_data["providers"], (dict, list)):
            providers_data = raw_data["providers"]
        else:
            providers_data = raw_data

        if isinstance(providers_data, list):
            providers_data = {p.get("id"): p for p in providers_data if isinstance(p, dict) and p.get("id")}

        for pid, pdata in providers_data.items():
            if not isinstance(pdata, dict):
                continue
            pid_clean = str(pid).strip().lower()
            name = pdata.get("name", pid_clean.capitalize())
            base_url = pdata.get("api") or pdata.get("base_url") or pdata.get("baseUrl")
            
            raw_env = pdata.get("env") or pdata.get("env_vars") or pdata.get("envVars")
            if isinstance(raw_env, str):
                env_vars = (raw_env,)
            elif isinstance(raw_env, (list, tuple)):
                env_vars = tuple(raw_env)
            else:
                env_vars = ()

            auth_methods = tuple(pdata.get("auth_methods") or pdata.get("authMethods") or ("api_key",))

            if pid_clean == "anthropic":
                protocol = "anthropic-messages"
            elif pid_clean in ("google", "gemini"):
                protocol = "gemini-native"
            elif pid_clean == "ollama":
                protocol = "ollama"
            elif pdata.get("protocol"):
                protocol = pdata.get("protocol")
            else:
                protocol = "openai-chat-completions"

            if pid_clean not in self._providers_map:
                self._providers_map[pid_clean] = ProviderDescriptor(
                    id=pid_clean,
                    name=name,
                    protocol=protocol,
                    base_url=base_url,
                    env_vars=env_vars,
                    auth_methods=auth_methods,
                    source="models.dev",
                )
            else:
                # Update existing descriptor base_url/env if absent
                existing = self._providers_map[pid_clean]
                if not existing.base_url and base_url:
                    self._providers_map[pid_clean] = ProviderDescriptor(
                        id=existing.id,
                        name=existing.name,
                        protocol=existing.protocol,
                        base_url=base_url,
                        env_vars=existing.env_vars or env_vars,
                        auth_methods=existing.auth_methods or auth_methods,
                        supports_model_discovery=existing.supports_model_discovery,
                        supports_custom_base_url=existing.supports_custom_base_url,
                        source=existing.source,
                    )

            # Parse models for this provider
            models_list = pdata.get("models", {})
            parsed_models: List[ModelDescriptor] = []

            if isinstance(models_list, dict):
                for mid, mdata in models_list.items():
                    if isinstance(mdata, dict):
                        parsed_models.append(self._parse_model_descriptor(pid_clean, str(mid), mdata))
            elif isinstance(models_list, list):
                for mdata in models_list:
                    if isinstance(mdata, dict) and "id" in mdata:
                        parsed_models.append(self._parse_model_descriptor(pid_clean, str(mdata["id"]), mdata))
                    elif isinstance(mdata, str):
                        parsed_models.append(ModelDescriptor(provider_id=pid_clean, id=mdata, name=mdata))

            if parsed_models:
                self._models_map[pid_clean] = parsed_models

    def _parse_model_descriptor(self, provider_id: str, model_id: str, mdata: Dict[str, Any]) -> ModelDescriptor:
        name = mdata.get("name") or model_id
        limit_data = mdata.get("limit", {}) if isinstance(mdata.get("limit"), dict) else {}
        ctx = limit_data.get("context") or mdata.get("context_window") or mdata.get("contextWindow") or mdata.get("max_context_length") or 8192
        max_out = limit_data.get("output") or mdata.get("max_output_tokens") or mdata.get("maxOutputTokens") or 4096

        supports_tools = bool(mdata.get("tool_call") or mdata.get("supports_tools") or mdata.get("supportsTools") or mdata.get("tools"))
        supports_reasoning = bool(mdata.get("reasoning") or mdata.get("supports_reasoning") or mdata.get("supportsReasoning"))
        supports_temp = bool(mdata.get("supports_temperature", True))
        supports_att = bool(mdata.get("attachment") or mdata.get("supports_attachments") or mdata.get("supportsAttachments"))

        modalities_data = mdata.get("modalities", {}) if isinstance(mdata.get("modalities"), dict) else {}
        in_mods = tuple(modalities_data.get("input", ["text"])) if isinstance(modalities_data.get("input"), (list, tuple)) else ("text",)
        out_mods = tuple(modalities_data.get("output", ["text"])) if isinstance(modalities_data.get("output"), (list, tuple)) else ("text",)

        cost_data = mdata.get("cost", {}) if isinstance(mdata.get("cost"), dict) else {}
        cost_in_raw = cost_data.get("input") if "input" in cost_data else mdata.get("cost_input")
        cost_out_raw = cost_data.get("output") if "output" in cost_data else mdata.get("cost_output")

        cost_in = Decimal(str(cost_in_raw)) if cost_in_raw is not None else None
        cost_out = Decimal(str(cost_out_raw)) if cost_out_raw is not None else None

        return ModelDescriptor(
            provider_id=provider_id,
            id=model_id,
            name=name,
            context_window=ctx,
            max_output_tokens=max_out,
            supports_tools=supports_tools,
            supports_reasoning=supports_reasoning,
            supports_temperature=supports_temp,
            supports_attachments=supports_att,
            input_modalities=in_mods,
            output_modalities=out_mods,
            cost_input=cost_in,
            cost_output=cost_out,
            raw_metadata=mdata,
        )

    def is_cache_stale(self) -> bool:
        if not self.cache_file.exists():
            return True
        try:
            mtime = self.cache_file.stat().st_mtime
            return (time.time() - mtime) > self.ttl_seconds
        except Exception:
            return True

    def refresh(self, force: bool = False) -> bool:
        """Fetches catalog from Models.dev and updates atomic cache."""
        if not force and not self.is_cache_stale():
            return True

        headers = {"User-Agent": "Kitt-Agent-CLI", "Accept": "application/json"}
        req = urllib.request.Request(self.fetch_url, headers=headers)
        try:
            with secure_urlopen(req, timeout=self.timeout) as resp:
                if resp.status == 200:
                    raw_text = resp.read().decode("utf-8")
                    data = json.loads(raw_text)
                    self._parse_and_merge_catalog(data)
                    self._write_cache_atomic(raw_text)
                    return True
        except Exception:
            # Fall back to existing cached/builtin catalog without failing
            return False
        return False

    def _write_cache_atomic(self, payload: str) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".models.dev.", dir=str(self.cache_dir))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as h:
                h.write(payload)
                h.flush()
                os.fsync(h.fileno())
            os.replace(tmp, self.cache_file)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def providers(self) -> List[ProviderDescriptor]:
        return list(self._providers_map.values())

    def provider(self, provider_id: str) -> Optional[ProviderDescriptor]:
        return self._providers_map.get((provider_id or "").strip().lower())

    def models(self, provider_id: Optional[str] = None) -> List[ModelDescriptor]:
        if provider_id:
            pid = provider_id.strip().lower()
            return list(self._models_map.get(pid, []))
        all_models = []
        for m_list in self._models_map.values():
            all_models.extend(m_list)
        return all_models
